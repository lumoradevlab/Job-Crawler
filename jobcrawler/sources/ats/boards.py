"""The slug list one ATS source crawls: its built-ins plus discovered ones."""


def board_list(ats, builtin, args):
    """The built-in slugs for one ATS, plus whatever --discover has found."""
    found = getattr(args, "boards_found", None) or {}
    return list(dict.fromkeys(list(builtin) + list(found.get(ats, []))))
