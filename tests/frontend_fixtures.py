from athena.auth import token_hash
from athena.frontend_state import observe


def observation(session, client_id='browser-one', revision=1, **state):
    return {'client_id': client_id, 'revision': revision, 'session_id': session,
            'foreground': True, 'viewport': {'width': 1200, 'height': 800}, **state}


async def observe_client(store, user, session, token, client_id='browser-one', revision=1, **state):
    return await observe(store, user, token_hash(token), observation(session, client_id, revision, **state))
