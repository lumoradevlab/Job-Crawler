"""The slug list one ATS source crawls: its built-ins plus discovered ones."""


def board_list(ats, builtin, ctx):
    """The built-in slugs for one ATS, plus whatever --discover has found."""
    return list(dict.fromkeys(list(builtin) + list(ctx.boards_found.get(ats, []))))
