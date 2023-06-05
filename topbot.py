from base64 import b64encode
from dotenv import load_dotenv
import os
import requests
import socket
from urllib.parse import (urlencode, parse_qs)
from uuid import uuid4


load_dotenv()


def authenticate(port=4242):
    client_id = os.getenv('CLIENT_ID')
    client_secret = os.getenv('CLIENT_SECRET')
    spotify_credentials = b64encode(f'{client_id}:{client_secret}'.encode('ascii')).decode('ascii')
    callback = f'http://localhost:{port}/callback'
    state = str(uuid4())
    scope = 'user-top-read'
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'scope': scope,
        'redirect_uri': callback,
        'state': state
    }
    auth_url = f'https://accounts.spotify.com/authorize?{urlencode(params)}'
    # Can't get webbrowser to open from within WSL so I'm giving up on that and requiring user to click link
    print(f'Authenticate here: {auth_url}')

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

def get_top_tracks(token=None, n=50):
    if not token:
        token = authenticate()
    
    api_endpoint = f'https://api.spotify.com/v1/me/top/tracks?time_range=short_term&limit={n}'
    headers = {'Authorization': f'Bearer {token}'}

    res = requests.get(api_endpoint, headers=headers)

    response_items = res.json()['items']
    top_tracks = [
        {
            'name': track['name'],
            'artists': [artist['name'] for artist in track['artists']],
            'id': track['id']
        }
        for track in response_items
    ]
    for track in top_tracks:
        print(track)
    
    return top_tracks


if __name__ == '__main__':
    get_top_tracks()