"""Local credentials: one private token file per fixed actor; never returned by HTTP."""
import hmac
import os
from pathlib import Path
from fastapi import Header, HTTPException, Request
from urllib.parse import urlsplit

ACTORS = {
    'salar': 'Salar', 'chief_of_staff': 'Chief of Staff', 'jimmy': 'Jimmy (SEO)',
    'merchandiser': 'Merchandiser', 'blogger': 'Blogger', 'social': 'Social',
    'price_analyst': 'Price Analyst', 'code_improver': 'Code Improver',
}
MANAGERS = {'salar', 'chief_of_staff'}


def token_directory():
    return Path(os.environ.get('TASK_MANAGER_TOKEN_DIR', '~/.config/shopifyseo/task-actors')).expanduser()


def authenticate(x_task_token: str = Header(default='')) -> str:
    if x_task_token:
        matches = []
        for actor in ACTORS:
            try:
                expected = (token_directory() / f'{actor}.token').read_text().strip()
            except FileNotFoundError:
                continue
            if len(expected) >= 32 and hmac.compare_digest(x_task_token.encode(), expected.encode()):
                matches.append(actor)
        if len(matches) == 1:
            return matches[0]
    raise HTTPException(401, 'A valid X-Task-Token is required')


def authenticate_web(request: Request) -> str:
    """Single-user web surface protected by the deployment's trusted network.

    Fetch Metadata and a custom header reject cross-site browser requests. They
    are CSRF defenses, not proof of a human identity; network access grants the
    web user Salar's role. Agent routes still require their own tokens.
    """
    headers = request.headers
    if (headers.get('sec-fetch-site') != 'same-origin'
            or headers.get('sec-fetch-mode') not in ('cors', 'same-origin')
            or headers.get('x-task-web') != '1'):
        raise HTTPException(403, 'Use the task manager from this app')
    origin = headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or parsed.netloc != headers.get('host'):
            raise HTTPException(403, 'Cross-origin task requests are not allowed')
    return 'salar'
