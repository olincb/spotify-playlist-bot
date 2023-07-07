from base64 import b64encode
from dotenv import load_dotenv
import os
import pprint
import requests
import socket
import time
from urllib.parse import (urlencode, parse_qs)
from uuid import uuid4


load_dotenv()

N_TRACKS = 25

class TopBot:

    def __init__(self):
        self.port = 4242
        self.callback_url = f'http://localhost:{self.port}/callback'

        self.spotify_scope = 'user-top-read playlist-modify-public'
        self.state = str(uuid4())

        self.spotify_client_id = os.getenv('CLIENT_ID')
        self.spotify_client_secret = os.getenv('CLIENT_SECRET')
        if not (self.spotify_client_id and self.spotify_client_secret):
            raise ValueError('Need Spotify Client ID and Secret.')
        self.spotify_credentials = b64encode(f'{self.spotify_client_id}:{self.spotify_client_secret}'.encode('ascii')).decode('ascii')
        self.auth_url = self.form_auth_url()


    def form_auth_url(self):
        params = {
            'response_type': 'code',
            'client_id': self.spotify_client_id,
            'scope': self.spotify_scope,
            'redirect_uri': self.callback_url,
            'state': self.state
        }
        auth_url = f'https://accounts.spotify.com/authorize?{urlencode(params)}'
        return auth_url


    def parse_code_from_request(self, req):
        endpoint = req.split()[1]
        query_string = endpoint.split('?')[1]
        req_params = parse_qs(query_string)
        recieved_state = req_params['state'][0]
        if recieved_state != self.state:
            print(f'State mismatch.\nGot:      {recieved_state}\nExpected: {self.state}')
            return None
        code = req_params['code'][0]
        return code


    def get_token(self, code):
        token_endpoint = 'https://accounts.spotify.com/api/token'
        post_body = {'code': code, 'redirect_uri': self.callback_url, 'grant_type': 'authorization_code'}
        headers = {'Authorization': f'Basic {self.spotify_credentials}', 'Content-Type': 'application/x-www-form-urlencoded'}
        r = requests.post(token_endpoint, data=post_body, headers=headers)
        token = r.json()['access_token']
        return token


    def format_return_lists(self, added, removed):
        """
        This modifies the arguments but it's fine because we don't need them anymore
        """
        for l in [added, removed]:
            for t in l:
                del t['id']
                del t['album_id']
                del t['uri']
                t['artists'] = [a['name'] for a in t['artists']]

        pp = pprint.PrettyPrinter(indent=4, width=140)
        added_str = pp.pformat(added)
        removed_str = pp.pformat(removed)
        return added_str, removed_str


    def update_top_tracks_playlist(self):

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('localhost', self.port))
            sock.listen()

            print(f'Authenticate here: {self.auth_url}')

            # Should be able to `while True:` the remainder of this.
            # I'll have to deal with token refreshing though.
            csock, caddr = sock.accept()
            req = csock.recv(1024).decode()

            try:
                code = self.parse_code_from_request(req)
                self.token = self.get_token(code)

                added, removed = self.do_update()

                if added or removed:
                    added_str, removed_str = self.format_return_lists(added, removed)
                    res = '\n'.join([
                        'HTTP/1.0 200 OK\n',
                        'Added:',
                        added_str,
                        'Removed:',
                        removed_str,
                        ''
                    ])
                else:
                    res = 'HTTP/1.0 200 OK\n\nNo changes to playlist.\n'
                csock.sendall(res.encode())
            except Exception as e:
                res = 'HTTP/1.0 500 Internal Server Error\n\nSomething went wrong. Sorry.\n'
                csock.sendall(res.encode())
                raise e
            finally:
                csock.close()


    def api_call(self, endpoint, method='get', payload={}):

        headers = {'Authorization': f'Bearer {self.token}'}
        if method == 'get':
            res = requests.get(endpoint, headers=headers)
        elif method == 'post':
            res = requests.post(endpoint, headers=headers, json=payload)
        elif method == 'put':
            res = requests.put(endpoint, headers=headers, json=payload)
        elif method == 'delete':
            res = requests.delete(endpoint, headers=headers, json=payload)
        else:
            raise NotImplemented(f'method {method} not implemented.')
        
        if 'application/json' in res.headers.get('Content-Type', ''):
            return res.json()
        else:
            return res.text

    def extract_meaningful_track_fields(self, tracks):
        return [
            {
                'name':         t['name'],
                'uri':          t['uri'],
                'id':           t['id'],
                'artists':      [
                                    {
                                        'name': a['name'],
                                        'id': a['id']
                                    }
                                    for a in t['artists']
                                ],
                'album_name':   t['album']['name'],
                'album_id':     t['album']['id']
            }
            for t in tracks
        ]

    def get_top_tracks(self, n=N_TRACKS):
        api_endpoint = f'https://api.spotify.com/v1/me/top/tracks?time_range=short_term&limit={n}'

        res = self.api_call(api_endpoint)

        tracks = res['items']
        top_tracks = self.extract_meaningful_track_fields(tracks)
        
        return top_tracks

    def get_users_id_and_name(self):
        api_endpoint = 'https://api.spotify.com/v1/me'

        res = self.api_call(api_endpoint)
        self.user_name = res['display_name']
        self.user_id = res['id']
        self.playlist_name = f"{self.user_name}'s Recent Top Tracks"

    def get_playlists(self):
        api_endpoint = 'https://api.spotify.com/v1/me/playlists?limit=50'

        res = self.api_call(api_endpoint)
        playlists = res['items']
        while res['next']:
            res = self.api_call(res['next'])
            playlists += res['items']

        playlists = [
            p for p in playlists 
            if p['owner']['id'] == self.user_id
            ]
        playlists = {p['name']: p['id'] for p in playlists}
        return playlists

    def create_top_tracks_playlist(self):
        api_endpoint = f'https://api.spotify.com/v1/users/{self.user_id}/playlists'
        body = {'name': self.playlist_name}
        res = self.api_call(api_endpoint, method='post', payload=body)
        plid = res['id']
        return plid

    def update_playlist_description(self):
        pl_description = f'Auto-generated. Updated {time.strftime("%B %-d, %Y")}.'
        api_endpoint = f'https://api.spotify.com/v1/playlists/{self.plid}'
        body = {'description': pl_description}
        res = self.api_call(api_endpoint, method='put', payload=body)
        print(res)

    def get_playlist_tracks(self, plid):
        api_endpoint = f'https://api.spotify.com/v1/playlists/{plid}/tracks'
        res = self.api_call(api_endpoint)
        tracks = res['items']
        while res['next']:
            res = self.api_call(res['next'])
            tracks += res['items']

        tracks = [t['track'] for t in tracks]
        tracks = self.extract_meaningful_track_fields(tracks)
        return tracks

    def remove_tracks(self, track_uris):
        api_endpoint = f'https://api.spotify.com/v1/playlists/{self.plid}/tracks'
        payload = {'uris': track_uris}
        res = self.api_call(api_endpoint, method='delete', payload=payload)

    def add_tracks(self, track_uris):
        api_endpoint = f'https://api.spotify.com/v1/playlists/{self.plid}/tracks'
        payload = {'uris': track_uris}
        res = self.api_call(api_endpoint, method='post', payload=payload)


    def do_update(self, n=N_TRACKS):
        # get user's info
        self.get_users_id_and_name()

        # create playlist if it doesn't exist
        playlists = self.get_playlists()
        if self.playlist_name in playlists:
            print('using existing playlist')
            self.plid = playlists[self.playlist_name]
        else:
            print('creating playlist')
            self.plid = self.create_top_tracks_playlist()

        # get playlist tracks
        pl_tracks = self.get_playlist_tracks(self.plid)
        pl_track_uris = [t['uri'] for t in pl_tracks]

        # get top tracks
        top_tracks = self.get_top_tracks()
        top_track_uris = [t['uri'] for t in top_tracks]

        print(f'num pl tracks:  {len(pl_tracks)}\n'
              f'num top tracks: {len(top_tracks)}')

        # remove playlist tracks no longer in top n tracks
        to_remove = {
            t['uri']: t
            for t in pl_tracks
            if not (t['uri'] in top_track_uris)
        }
        print(f'removing {len(to_remove)} tracks')
        if to_remove:
            self.remove_tracks(list(to_remove.keys()))

        
        # add remaining top tracks to playlist
        to_add = {
            t['uri']: t
            for t in top_tracks
            if not (t['uri'] in pl_track_uris)
        }
        print(f'adding {len(to_add)} tracks')
        if to_add:
            self.add_tracks(list(to_add.keys()))

        # update the playlist description
        self.update_playlist_description()

        return list(to_add.values()), list(to_remove.values())



if __name__ == '__main__':
    tb = TopBot()
    tb.update_top_tracks_playlist()
