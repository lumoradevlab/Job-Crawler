"""Reading credentials from the environment, and from a git-ignored .env.

Every key this project uses is read from an environment variable and never
from a file that could be committed. That is the whole policy, and it exists
because the alternative — a config file holding live keys — is one `git add`
away from publishing them to a public repository.

A .env file is supported because typing five exports into every new shell is
the reason people give up and hardcode the key instead. It is git-ignored,
it is only ever read (never written), and the real environment always wins
over it — so CI, where the secrets arrive as environment variables, needs no
.env at all and cannot be affected by a stray one.
"""

import os

ENV_FILE = ".env"


def load_env_file(path=ENV_FILE):
    """Merge KEY=value lines from a .env into os.environ, without overriding.

    A variable already set in the real environment is left alone: on CI the
    secrets arrive that way, and a .env accidentally shipped in a checkout
    must never be able to shadow them.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return {}

    loaded = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # "export FOO=bar" is what people paste in from a shell session.
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        # Strip one matched pair of quotes; a value containing '#' is common
        # in tokens, so no comment-stripping happens after the '='.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def redact(secret):
    """A token rendered safe to print: enough to identify, not to use.

    Logs and CI output are read by people who are not the key's owner, and a
    bot that prints its own token into a public Actions log has leaked it as
    surely as committing it would.
    """
    if not secret:
        return "(unset)"
    tail = secret[-4:] if len(secret) > 8 else ""
    return "…" + tail if tail else "(set)"
