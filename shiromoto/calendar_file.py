"""An .ics calendar file for one booking (RFC 5545), so it goes into a phone's calendar.

Written by hand, because it's short and it's good date practice:
- the times are on the shop's clock, with TZID=Asia/Manila, and the file carries
  a VTIMEZONE block saying what that zone's offset is (+08:00, no daylight saving);
- DTSTAMP is always UTC (the "Z" at the end);
- text escapes \\ ; , and newlines, and long lines are folded at 75 bytes.
"""
from .dates import to_local, zone


def escape(text):
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """Lines longer than 75 bytes continue on the next line, which starts with a space.
    Cut by bytes, but never in the middle of a character (₱ is 3 bytes)."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        encoded = char.encode()
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode())
            current = b""
        current += encoded
    parts.append(current.decode())
    return "\r\n ".join(parts)


def offset_text(delta):
    """timedelta(hours=8) -> "+0800"."""
    total = int(delta.total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    hours, mins = divmod(abs(total), 60)
    return f"{sign}{hours:02d}{mins:02d}"


def booking_ics(appointment, shop, now, link):
    tz = shop.timezone
    starts, ends = to_local(appointment.starts_at, tz), to_local(appointment.ends_at, tz)
    offset = offset_text(zone(tz).utcoffset(starts.replace(tzinfo=None)))
    services = ", ".join(line.name for line in appointment.lines)
    description = (f"Booking {appointment.ref}: {services} for {appointment.vehicle.label}.\n"
                   f"See, move or cancel it: {link}")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ShiroMoto Garage//Bookings//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VTIMEZONE",
        f"TZID:{tz}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        f"TZOFFSETFROM:{offset}",
        f"TZOFFSETTO:{offset}",
        f"TZNAME:{starts.tzname()}",
        "END:STANDARD",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        f"UID:{appointment.ref}@shiromoto",
        f"DTSTAMP:{now:%Y%m%dT%H%M%S}Z",
        f"DTSTART;TZID={tz}:{starts:%Y%m%dT%H%M%S}",
        f"DTEND;TZID={tz}:{ends:%Y%m%dT%H%M%S}",
        f"SUMMARY:{escape(services + ' at ' + shop.name)}",
        f"LOCATION:{escape(shop.address or shop.name)}",
        f"DESCRIPTION:{escape(description)}",
        f"SEQUENCE:{appointment.version}",  # a moved booking replaces the old event
        "STATUS:" + ("CANCELLED" if appointment.status == "cancelled" else "CONFIRMED"),
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape(services + ' tomorrow at ' + shop.name)}",
        "TRIGGER:-PT18H",  # the evening before a morning booking
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"

