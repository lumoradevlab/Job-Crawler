"""What a stranger sees, and the buttons that make them a subscriber.

Everything a newcomer has to answer is a button. A bot that asks someone to
type a role name has already lost most of them, and the two questions worth
asking — what do you build, where can you work — are both closed sets that
already exist in the crawler: the seventeen role profiles and the two
countries.

The commands are deliberately small and include the ones that let someone
leave. A bot you cannot leave is a bot people block instead, and a block is
worse than an unsubscribe in both directions: the reader has to do more work
to escape, and the bot keeps spending a request per run forever finding out.
"""

from ..filters.countries import COUNTRIES
from ..roles import PROFILES

# Presented in the order someone is likely to want them, not alphabetically:
# the common disciplines first, the umbrellas last so "all" is a considered
# choice rather than the first thing a thumb lands on.
ROLE_ORDER = ("backend", "frontend", "fullstack", "android", "ios", "mobile",
              "devops", "data", "qa", "security", "embedded", "gamedev",
              "itsupport", "product", "design", "software", "all")

WELCOME = (
    "<b>Remote software jobs, twice a day.</b>\n\n"
    "Postings from company job boards and the big aggregators, filtered to "
    "genuinely remote roles you can hold where you live.\n\n"
    "What do you build?"
)

COUNTRY_PROMPT = "Where can you work?"

HELP = (
    "<b>Remote software jobs, twice a day.</b>\n\n"
    "Under each job:\n"
    "🚀 <b>Apply</b> — opens the posting\n"
    "✅ <b>I applied</b> — tracked in /applied\n"
    "🔖 <b>Save</b> — kept in /saved\n"
    "🚫 <b>Not for me</b> — never sent again\n"
    "🔕 <b>Mute company</b> — nothing from them again\n\n"
    "<b>Commands</b>\n"
    "/settings — change your role or country\n"
    "/applied /saved /muted — what you have marked\n"
    "/pause — stop the jobs, keep your settings\n"
    "/resume — start them again\n"
    "/stop — delete everything and unsubscribe\n\n"
    "<i>Jobs arrive twice a day. Taps are read at the next run.</i>"
)


def role_keyboard(chosen=()):
    """The role picker. Chosen roles are ticked, so it doubles as /settings.

    Three per row: the names are short enough to fit and a phone shows them
    without truncation, where four starts eliding the longer ones.
    """
    chosen = set(chosen or ())
    buttons = [{"text": ("✓ " if r in chosen else "") + PROFILES[r].name,
                "callback_data": f"role:{r}"}
               for r in ROLE_ORDER if r in PROFILES]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    # Done is its own row and always last: it is the only button that ends
    # the step, and mixing it into a grid of toggles invites a misfire.
    rows.append([{"text": "Done →", "callback_data": "role:done"}])
    return {"inline_keyboard": rows}


def country_keyboard(chosen=None):
    return {"inline_keyboard": [[
        {"text": ("✓ " if c.code == chosen else "") + f"{c.flag} {c.name}",
         "callback_data": f"country:{c.code}"}
        for c in COUNTRIES.values()
    ]]}


def settings_text(sub):
    """What this subscriber currently gets, in their own words."""
    country = COUNTRIES.get(sub.country)
    names = ", ".join(PROFILES[r].name for r in sub.roles if r in PROFILES)
    return (f"<b>Your feed</b>\n\n"
            f"Roles: {names or '—'}\n"
            f"Where: {country.flag} {country.name}\n"
            f"{'⏸ Paused' if sub.paused else '▶️ Active'}"
            f" · since {sub.joined}\n\n"
            f"Tap to change:")


def confirmation(sub):
    country = COUNTRIES.get(sub.country)
    names = ", ".join(PROFILES[r].name for r in sub.roles if r in PROFILES)
    return (f"<b>Set.</b> {names} roles, remote in {country.name}.\n\n"
            f"First batch at the next run — twice a day, morning and "
            f"evening.\n\n"
            f"/settings to change · /help for everything else")
