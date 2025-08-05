#!/usr/bin/env python3
"""
ipo_email.py
~~~~~~~~~~~~~~~~

This script fetches the upcoming initial public offerings (IPOs) from the
StockAnalysis IPO calendar and emails a concise report to a recipient. The
report includes the IPO date, ticker symbol, company name, exchange,
projected price range, shares offered, deal size and the expected market
capitalization (taken from the calendar). The script is designed to run
weekly, either manually or via a scheduled job such as a cron entry or a
GitHub Actions workflow.

The email is sent via Gmail's SMTP servers. For security reasons, you
should create an app password in your Google account and provide it to
the script via environment variables (see configuration below). Using an
app password ensures that you do not expose your main Google account
password and allows you to revoke access at any time.

Configuration
-------------
The following environment variables control the script's behaviour:

```
GMAIL_USER    The Gmail address that will send the email.
GMAIL_PASS    An app password generated from the Gmail account.
RECIPIENT     The email address that will receive the report.
TIMEZONE      (optional) IANA time zone for interpreting IPO dates and
              formatting the email's timestamp. Defaults to
              'America/New_York' if unset.
```

Usage
-----
Run the script directly with `python3 ipo_email.py`. It will fetch IPO
data, compose a report and send an email immediately. To schedule the
script to run weekly, you can either use cron (on a UNIX-like system) or
configure a GitHub Actions workflow. An example workflow is provided in
the repository (`.github/workflows/weekly_ipo_email.yml`).

Note
----
This script scrapes the table from StockAnalysis's public IPO calendar to
obtain the upcoming IPO list. The calendar includes columns for the
IPO date, ticker symbol, company name, exchange, price range, shares
offered, deal size, market cap and revenue. We parse these fields and
include them in the email. See the StockAnalysis page for an example of
the table structure, where each row lists the IPO date, symbol, company
name, exchange, price range, shares offered, deal size and expected
market cap【153199201465310†L83-L96】.
"""

import os
import sys
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup
import pytz


def get_upcoming_ipos(url: str = "https://stockanalysis.com/ipos/calendar/") -> List[Dict[str, str]]:
    """Fetch and parse upcoming IPOs from StockAnalysis.

    Args:
        url: The URL of the IPO calendar. Defaults to the StockAnalysis
            calendar page.

    Returns:
        A list of dictionaries, each representing an IPO with the following
        keys: 'date', 'symbol', 'company', 'exchange', 'price_range',
        'shares_offered', 'deal_size', 'market_cap', and 'revenue'. Dates
        remain as strings to make the email more readable; you can convert
        them to ``datetime.date`` objects using ``parse_date`` below if
        additional filtering is desired.

    Raises:
        requests.HTTPError: If the HTTP request fails.
        ValueError: If parsing fails due to unexpected page structure.
    """
    logging.debug(f"Fetching IPO calendar from {url}")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # Locate the table body. The calendar uses multiple tables for different
    # time windows (e.g. "This Week", "After Next Week"), but each table
    # shares the same column structure. We select all <tbody> elements to
    # gather every row on the page.
    bodies = soup.find_all("tbody")
    if not bodies:
        raise ValueError("Unable to locate IPO table body on the calendar page")

    ipos: List[Dict[str, str]] = []
    for tbody in bodies:
        for row in tbody.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 8:
                # Skip rows that don't have the expected number of columns.
                continue
            try:
                ipo_date = cells[0].get_text(strip=True)
                symbol = cells[1].get_text(strip=True)
                company = cells[2].get_text(strip=True)
                exchange = cells[3].get_text(strip=True)
                price_range = cells[4].get_text(strip=True)
                shares_offered = cells[5].get_text(strip=True)
                deal_size = cells[6].get_text(strip=True)
                market_cap = cells[7].get_text(strip=True)
                # Revenue column is optional and may be missing (represented
                # by '-'); guard accordingly.
                revenue = cells[8].get_text(strip=True) if len(cells) > 8 else "-"
            except IndexError:
                logging.warning("Skipping row with unexpected structure: %s", row)
                continue

            ipos.append({
                "date": ipo_date,
                "symbol": symbol,
                "company": company,
                "exchange": exchange,
                "price_range": price_range,
                "shares_offered": shares_offered,
                "deal_size": deal_size,
                "market_cap": market_cap,
                "revenue": revenue,
            })
    logging.debug(f"Parsed {len(ipos)} IPO entries from the calendar")
    return ipos


def parse_date(date_str: str, tz: pytz.timezone) -> date:
    """Convert a date string like 'Aug 4, 2025' to a datetime.date.

    Args:
        date_str: The date string from the table (e.g. 'Aug 4, 2025').
        tz: A timezone object used to normalise the resulting date to local
            time. Note that the IPO calendar dates are given in U.S. market
            context, so using the local timezone ensures accurate date
            comparisons when filtering upcoming events.

    Returns:
        A ``datetime.date`` object representing the given date in the
        specified timezone.
    """
    parsed = datetime.strptime(date_str, "%b %d, %Y")
    # Attach timezone to the naive datetime. Converting to date will drop
    # timezone information. This step is primarily for clarity and future
    # modifications; in practice we could return parsed.date().
    localized = tz.localize(parsed)
    return localized.date()


def filter_upcoming(ipos: List[Dict[str, str]], days_ahead: int = 7,
                    tz: Optional[pytz.timezone] = None) -> List[Dict[str, str]]:
    """Filter IPOs occurring within the next ``days_ahead`` days.

    Args:
        ipos: A list of IPO dictionaries as returned by ``get_upcoming_ipos``.
        days_ahead: The number of days ahead (including today) to include.
        tz: Timezone used to interpret the IPO dates. If ``None`` is passed,
            the timezone will default to 'America/New_York'.

    Returns:
        A filtered list of IPO dictionaries occurring within the
        upcoming window.
    """
    if tz is None:
        tz = pytz.timezone(os.getenv('TIMEZONE', 'America/New_York'))
    today = datetime.now(tz).date()
    end_date = today + timedelta(days=days_ahead)
    upcoming: List[Dict[str, str]] = []
    for ipo in ipos:
        try:
            ipo_date = parse_date(ipo['date'], tz)
        except Exception:
            # If parsing fails, skip the entry.
            continue
        if today <= ipo_date <= end_date:
            upcoming.append(ipo)
    return upcoming


def compose_email(ipos: List[Dict[str, str]], tz: Optional[pytz.timezone] = None) -> MIMEMultipart:
    """Create a MIME multipart email summarizing the IPOs.

    Args:
        ipos: List of IPO dictionaries to include in the email.
        tz: Optional timezone for timestamp formatting. Defaults to
            'America/New_York' if unspecified.

    Returns:
        A ``MIMEMultipart`` email object ready for sending.
    """
    if tz is None:
        tz = pytz.timezone(os.getenv('TIMEZONE', 'America/New_York'))

    now = datetime.now(tz)
    subject_date = now.strftime('%B %d, %Y')
    subject = f"Upcoming IPOs – Week of {subject_date}"
    sender = os.environ['GMAIL_USER']
    recipient = os.environ['RECIPIENT']

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = recipient

    if not ipos:
        plain_text = (
            f"There are no IPOs scheduled within the upcoming week as of {subject_date}."
        )
        msg.attach(MIMEText(plain_text, 'plain'))
        msg.attach(MIMEText(f"<p>{plain_text}</p>", 'html'))
        return msg

    # Build plain-text and HTML versions of the email body.
    lines = []
    html_rows = []
    header = (
        f"IPO Date | Symbol | Company | Exchange | Price Range | Shares Offered | "
        f"Deal Size | Market Cap\n"
        f"{'-' * 80}"
    )
    lines.append(header)
    for ipo in ipos:
        line = (
            f"{ipo['date']} | {ipo['symbol']} | {ipo['company']} | {ipo['exchange']} | "
            f"{ipo['price_range']} | {ipo['shares_offered']} | {ipo['deal_size']} | "
            f"{ipo['market_cap']}"
        )
        lines.append(line)
        html_rows.append(
            f"<tr>"
            f"<td>{ipo['date']}</td>"
            f"<td>{ipo['symbol']}</td>"
            f"<td>{ipo['company']}</td>"
            f"<td>{ipo['exchange']}</td>"
            f"<td>{ipo['price_range']}</td>"
            f"<td>{ipo['shares_offered']}</td>"
            f"<td>{ipo['deal_size']}</td>"
            f"<td>{ipo['market_cap']}</td>"
            f"</tr>"
        )

    plain_body = "\n".join(lines)
    html_body = (
        f"<html><body>"
        f"<p>Upcoming IPOs scheduled within the next week as of {subject_date}:</p>"
        f"<table border='1' cellpadding='5' cellspacing='0'>"
        f"<thead>"
        f"<tr>"
        f"<th>IPO Date</th><th>Symbol</th><th>Company</th><th>Exchange</th>"
        f"<th>Price Range</th><th>Shares Offered</th><th>Deal Size</th><th>Market Cap</th>"
        f"</tr>"
        f"</thead>"
        f"<tbody>"
        f"{''.join(html_rows)}"
        f"</tbody>"
        f"</table>"
        f"<p style='margin-top:1em;'>This information is sourced from StockAnalysis' IPO calendar. "
        f"Please note that IPO dates and valuations may change as companies update "
        f"their filings【153199201465310†L83-L96】.</p>"
        f"</body></html>"
    )

    msg.attach(MIMEText(plain_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))
    return msg


def send_email(msg: MIMEMultipart) -> None:
    """Send the provided email message via Gmail's SMTP server.

    Args:
        msg: A MIME multipart email object created by ``compose_email``.

    Raises:
        smtplib.SMTPException: If an error occurs while sending the email.
    """
    gmail_user = os.environ['GMAIL_USER']
    gmail_password = os.environ['GMAIL_PASS']
    recipient = os.environ['RECIPIENT']

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, [recipient], msg.as_string())
    logging.info("Email sent successfully to %s", recipient)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        all_ipos = get_upcoming_ipos()
        logging.info(f"Fetched {len(all_ipos)} total IPO entries from calendar")
        upcoming_ipos = filter_upcoming(all_ipos, days_ahead=7)
        logging.info(f"Found {len(upcoming_ipos)} IPOs occurring in the next week")
        email_msg = compose_email(upcoming_ipos)
        send_email(email_msg)
    except Exception as exc:
        logging.exception("An error occurred during IPO email processing: %s", exc)
        sys.exit(1)


if __name__ == '__main__':
    main()