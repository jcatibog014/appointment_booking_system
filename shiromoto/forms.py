"""Reading form fields: every function returns (value, problem), where problem is
a message for the person or None. Views collect the problems into an errors dict."""
import re

from .dates import minutes, parse_day, parse_time

STEP = 15  # every time in the shop is on a quarter hour: 8:00, 8:15, 8:30...


def text(form, name, label, max_length, required=True):
    value = (form.get(name) or "").strip()
    if required and not value:
        return value, f"Enter {label}."
    if len(value) > max_length:
        return value, f"Use at most {max_length} characters."
    return value, None


def pesos(form, name):
    """"1,250.50" or "₱1250" -> 125050 centavos. No floats: "0.1 + 0.2" money bugs never start."""
    raw = (form.get(name) or "").strip().replace(",", "").replace("₱", "").strip()
    if not raw:
        return 0, None
    match = re.fullmatch(r"(\d{1,7})(?:\.(\d{1,2}))?", raw)
    if not match:
        return None, "Write an amount like 1500 or 1,250.50."
    whole, cents = match.groups()
    return int(whole) * 100 + int((cents or "0").ljust(2, "0")), None


def whole_number(form, name, low, high, label="a number"):
    try:
        value = int(form.get(name, ""))
    except ValueError:
        return None, f"Choose {label}."
    if not low <= value <= high:
        return None, f"Choose {label} from {low} to {high}."
    return value, None


def quarter_time(form, name, required=True):
    """A time on a quarter hour (8:00, 8:15...), so bookings line up with the 15-minute slots."""
    raw = form.get(name)
    value = parse_time(raw)
    if value is None:
        return None, "Choose a time." if required or raw else None
    if value.minute % STEP or value.second:
        return None, "Use a time on the quarter hour, like 8:00, 8:15 or 8:30."
    return value, None


def day(form, name):
    value = parse_day(form.get(name))
    return value, None if value else "Choose a day."


def time_window(starts, ends, label="The end"):
    """Problem with a window, or None. Both must be set and in order."""
    if starts and ends and minutes(ends) <= minutes(starts):
        return f"{label} must be later than the start."
    return None


def inside(window_start, window_end, outer_start, outer_end):
    """True if the window sits within the outer window."""
    return minutes(outer_start) <= minutes(window_start) and minutes(window_end) <= minutes(outer_end)


def checked(form, name):
    return form.get(name) in ("1", "on", "yes", "true")

