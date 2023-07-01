from base64 import b64encode
from dotenv import load_dotenv
import os
import requests
import socket
import time
from urllib.parse import (urlencode, parse_qs)
from uuid import uuid4

normal_print = print
from rich import print


load_dotenv()

N_TRACKS = 25

class TopBot:

    def __init__(self):
        self.token = self.authenticate()
        self.get_users_id_and_name()
        self.pl_description = f'Auto-generated. Updated {time.strftime("%B %-d, %Y")}.'

    def authenticate(self, port=4242):
        client_id = os.getenv('CLIENT_ID')
        client_secret = os.getenv('CLIENT_SECRET')
        spotify_credentials = b64encode(f'{client_id}:{client_secret}'.encode('ascii')).decode('ascii')
        callback = f'http://localhost:{port}/callback'
        state = str(uuid4())
        scope = 'user-top-read playlist-modify-public' # user-read-private user-read-email'
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'scope': scope,
            'redirect_uri': callback,
            'state': state
        }
        auth_url = f'https://accounts.spotify.com/authorize?{urlencode(params)}'
        # Can't get webbrowser to open from within WSL so I'm giving up on that and requiring user to click link
        normal_print(f'Authenticate here: {auth_url}')

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('localhost', port))
            sock.listen()
            csock, caddr = sock.accept()
            req = csock.recv(1024).decode()
            res = 'HTTP/1.0 200 OK\n\nAuthentication successful. You may return to your terminal.\n'
            csock.sendall(res.encode())
            csock.close()
        finally:
            sock.close()

        endpoint = req.split()[1]
        query_string = endpoint.split('?')[1]
        req_params = parse_qs(query_string)
        recieved_state = req_params['state'][0]
        if recieved_state != state:
            print(f'State mismatch.\nGot:      {recieved_state}\nExpected: {state}')
            return None
        code = req_params['code'][0]

        token_endpoint = 'https://accounts.spotify.com/api/token'
        post_body = {'code': code, 'redirect_uri': callback, 'grant_type': 'authorization_code'}
        headers = {'Authorization': f'Basic {spotify_credentials}', 'Content-Type': 'application/x-www-form-urlencoded'}
        r = requests.post(token_endpoint, data=post_body, headers=headers)
        token = r.json()['access_token']
        return token

    def api_call(self, endpoint, method='get', payload={}):
        if not self.token:
            self.authenticate()

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
        api_endpoint = f'https://api.spotify.com/v1/playlists/{self.plid}'
        body = {'description': self.pl_description}
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


    def update_top_tracks_playlist(self, n=N_TRACKS):

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
        to_remove = [
            t for t in pl_track_uris
            if not (t in top_track_uris)
        ]
        print(f'removing {len(to_remove)} tracks')
        if to_remove:
            self.remove_tracks(to_remove)

        
        # add remaining top tracks to playlist
        to_add = [
            t for t in top_track_uris
            if not (t in pl_track_uris)
        ]
        print(f'adding {len(to_add)} tracks')
        if to_add:
            self.add_tracks(to_add)

        # update the playlist description
        self.update_playlist_description()



if __name__ == '__main__':
    tb = TopBot()
    tb.update_top_tracks_playlist()
