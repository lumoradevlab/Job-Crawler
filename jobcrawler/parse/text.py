"""Company-name normalisation, shared by dedupe and ATS slug discovery."""

import re


# Words that live in a company's legal name and never in its ATS slug.
COMPANY_NOISE = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|plc|nv|bv|ag|sa|srl|"
    r"pty|holdings|group|company|technologies|solutions)\b\.?", re.I)
