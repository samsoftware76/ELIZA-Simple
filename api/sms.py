"""
Africa's Talking SMS integration for ELIZA Project Management App.

Sending only - no per-SMS billing/charging logic, no delivery-status
tracking, no queue (explicitly out of scope for this round). Twilio could
slot in later behind the same send_sms(to, message) signature if a second
provider is ever needed, but Africa's Talking is the standard local
gateway for Uganda so that's what this targets now.
"""
import os

from dotenv import load_dotenv

# Load environment variables from the ELIZA_App/.env file, same as api/payment.py.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

# Africa's Talking credentials - env-var driven the same way PesaPal's are
# configured in api/payment.py / api/admin.py. No hardcoded fallback: add
# AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY to the environment
# (Vercel project settings in production, .env locally) to enable sending.
AFRICASTALKING_USERNAME = os.getenv('AFRICASTALKING_USERNAME')
AFRICASTALKING_API_KEY = os.getenv('AFRICASTALKING_API_KEY')

if not (AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY):
    print("WARNING: Africa's Talking is not configured. Set AFRICASTALKING_USERNAME "
          "and AFRICASTALKING_API_KEY in the environment to enable SMS sending.")


def send_sms(to, message):
    """Send a single SMS via Africa's Talking.

    Returns {'success': bool, 'message': str} and never raises - missing
    config, a missing phone number, or an API-side failure all come back
    as a graceful result instead of crashing the caller, same degradation
    pattern as PesaPal's "not configured" handling in api/payment.py.
    """
    if not (AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY):
        return {
            'success': False,
            'message': "SMS isn't set up yet. Ask an admin to set AFRICASTALKING_USERNAME "
                       "and AFRICASTALKING_API_KEY to enable sending."
        }

    if not to:
        return {'success': False, 'message': 'This client has no phone number on file.'}

    # Imported lazily so a missing package degrades gracefully instead of
    # crashing the whole app at import time if requirements haven't been
    # reinstalled yet in some environment.
    try:
        import africastalking
    except ImportError:
        return {'success': False, 'message': "SMS isn't set up yet: the africastalking package is not installed."}

    try:
        africastalking.initialize(AFRICASTALKING_USERNAME, AFRICASTALKING_API_KEY)
        sms = africastalking.SMS
        response = sms.send(message, [to])

        # Africa's Talking reports success/failure per-recipient inside
        # SMSMessageData rather than via HTTP status, so a "200 OK" response
        # can still mean the message was rejected - check the per-recipient
        # status before calling it a success.
        recipients = response.get('SMSMessageData', {}).get('Recipients', [])
        if recipients and recipients[0].get('status') == 'Success':
            return {'success': True, 'message': f'SMS sent to {to}.'}

        status = recipients[0].get('status') if recipients else 'Unknown error'
        return {'success': False, 'message': f'SMS failed to send: {status}'}
    except Exception as e:
        return {'success': False, 'message': f'SMS failed to send: {str(e)}'}
