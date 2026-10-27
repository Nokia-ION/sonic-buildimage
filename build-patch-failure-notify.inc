# Sourced by build.sh — one summary email per submodule_prs when steps fail (see
# SONIC_BUILD_SH_PATCH_FAILURES). Email is sent only when CI_JOB_ID is set (GitLab CI).
# SMTP relay from GitLab CI/CD variables (only when CI_JOB_ID is set):
#   SONIC_PATCH_FAILURE_SMTP_SERVER, SONIC_PATCH_FAILURE_SMTP_PORT,
#   SONIC_PATCH_FAILURE_SMTP_USER, SONIC_PATCH_FAILURE_SMTP_PASSWORD,
#   SONIC_PATCH_FAILURE_SMTP_FROM, SONIC_PATCH_FAILURE_SMTP_STARTTLS
# Set PATCH_FAILURE_NOTIFY_TO="" to disable (still only in CI).

# Args: subject, body (multiline ok)
_send_patch_failure_email() {
  local subject="$1"
  local body="$2"
  local to="${SONIC_PATCH_FAILURE_SMTP_FROM}"
  local bodyfile

  [ -n "${CI_JOB_ID:-}" ] || return 0
  [ -n "$to" ] || return 0

  if ! command -v python3 >/dev/null 2>&1; then
    echo "WARNING: python3 missing; patch failure email not sent (CI)." >&2
    return 0
  fi

  bodyfile=$(mktemp) || { echo "WARNING: mktemp failed; patch failure email not sent." >&2; return 0; }
  chmod 600 "$bodyfile"
  printf '%s' "$body" >"$bodyfile" || { rm -f "$bodyfile"; return 0; }

  export PATCH_MAIL_BODY_FILE="$bodyfile"
  export PATCH_MAIL_SUBJECT="$subject"
  export PATCH_MAIL_TO="$to"
  export SMTP_SERVER="${SONIC_PATCH_FAILURE_SMTP_SERVER:-}"
  export SMTP_PORT="${SONIC_PATCH_FAILURE_SMTP_PORT:-}"
  export SMTP_USER="${SONIC_PATCH_FAILURE_SMTP_USER:-}"
  export SMTP_PASSWORD="${SONIC_PATCH_FAILURE_SMTP_PASSWORD:-}"
  export SMTP_FROM="${SONIC_PATCH_FAILURE_SMTP_FROM:-}"
  export SMTP_STARTTLS="${SONIC_PATCH_FAILURE_SMTP_STARTTLS:-}"

  if ! python3 <<'PATCH_MAIL_PY'
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)

body_path = os.environ.get("PATCH_MAIL_BODY_FILE")
if not body_path:
    fail("PATCH_MAIL_BODY_FILE is not set")
try:
    with open(body_path, encoding="utf-8", errors="replace") as f:
        body = f.read()
finally:
    try:
        os.remove(body_path)
    except OSError:
        pass

host = os.environ.get("SMTP_SERVER", "").strip()
if not host:
    fail("SMTP_SERVER is empty")

port = int(os.environ.get("SMTP_PORT", "587"))
to_addr = os.environ.get("PATCH_MAIL_TO", "").strip()
subj = os.environ.get("PATCH_MAIL_SUBJECT", "patch failure")
frm = (
    os.environ.get("SMTP_FROM", "").strip()
    or os.environ.get("GITLAB_USER_EMAIL", "").strip()
    or "gitlab-ci@noreply"
)

extra = ""
for key in ("CI_JOB_URL", "CI_PIPELINE_URL", "CI_PROJECT_PATH", "CI_COMMIT_SHA"):
    val = os.environ.get(key)
    if val:
        extra += f"\n{key}: {val}"
if extra:
    body = body + "\n" + extra

msg = EmailMessage()
msg["Subject"] = subj
msg["To"] = to_addr
msg["From"] = frm
msg.set_content(body)

user = os.environ.get("SMTP_USER", "").strip()
password = os.environ.get("SMTP_PASSWORD", "")

_start = os.environ.get("SMTP_STARTTLS", "1").strip().lower()
starttls = _start not in ("0", "false", "no", "off")

try:
    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.ehlo()
            if starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
except Exception as ex:
    fail("SMTP send failed: {!r}".format(ex))
PATCH_MAIL_PY
  then
    echo "WARNING: SMTP patch failure notice could not be sent to $to" >&2
  else
    echo "SMTP patch failure notice sent to $to"
  fi

  unset PATCH_MAIL_BODY_FILE PATCH_MAIL_SUBJECT PATCH_MAIL_TO \
        SMTP_SERVER SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_STARTTLS || true
  rm -f "$bodyfile"
  return 0
}
