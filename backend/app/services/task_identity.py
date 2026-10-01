"""Local credentials: one private token file per fixed actor; never returned by HTTP."""
import hmac
import os
from pathlib import Path
from fastapi import Header, HTTPException

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
