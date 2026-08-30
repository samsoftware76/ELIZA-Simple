"""
PesaPal payment integration for ELIZA Project Management App
"""
import os
import requests
import json
import uuid
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

# PesaPal API configuration
PESAPAL_BASE_URL = os.getenv('PESAPAL_BASE_URL', 'https://pay.pesapal.com/v3')
PESAPAL_CONSUMER_KEY = os.getenv('PESAPAL_CONSUMER_KEY')
PESAPAL_CONSUMER_SECRET = os.getenv('PESAPAL_CONSUMER_SECRET')
PESAPAL_IPN_ID = os.getenv('PESAPAL_IPN_ID')

# Debug PesaPal configuration
print(f"PesaPal Configuration:")
print(f"BASE_URL: {PESAPAL_BASE_URL}")
print(f"CONSUMER_KEY: {'Set' if PESAPAL_CONSUMER_KEY else 'Not set'}")
print(f"CONSUMER_SECRET: {'Set' if PESAPAL_CONSUMER_SECRET else 'Not set'}")
print(f"IPN_ID: {'Set' if PESAPAL_IPN_ID else 'Not set'}")

# Fallback to hardcoded values if environment variables are not set
if not PESAPAL_CONSUMER_KEY:
    PESAPAL_CONSUMER_KEY = 'QnXX9TFya0Zy+I+7S5Er5rjp1xwJY8sv'
    print(f"Using fallback CONSUMER_KEY")
    
if not PESAPAL_CONSUMER_SECRET:
    PESAPAL_CONSUMER_SECRET = 'r7yO64EsF56cmQWEGo04GRrYvvI='
    print(f"Using fallback CONSUMER_SECRET")
    
if not PESAPAL_IPN_ID:
    PESAPAL_IPN_ID = 'fe052a3e-786b-4c55-9d24-8e28dd3f80b5'
    print(f"Using fallback IPN_ID")

class PesaPalPayment:
    """PesaPal payment integration class"""
    
    def __init__(self):
        """Initialize PesaPal payment integration"""
        self.base_url = PESAPAL_BASE_URL
        self.consumer_key = PESAPAL_CONSUMER_KEY
        self.consumer_secret = PESAPAL_CONSUMER_SECRET
        self.ipn_id = PESAPAL_IPN_ID
        self.auth_token = None
        self.token_expiry = None
    
    def get_auth_token(self):
        """Get authentication token from PesaPal API"""
        if self.auth_token and self.token_expiry and self.token_expiry > datetime.now():
            print(f"Using cached auth token, valid until {self.token_expiry}")
            return self.auth_token
        
        # Debug credentials
        print(f"Requesting new auth token with:")
        print(f"Consumer Key: {self.consumer_key[:5]}...{self.consumer_key[-5:] if self.consumer_key else 'None'}")
        print(f"Consumer Secret: {self.consumer_secret[:5]}...{self.consumer_secret[-5:] if self.consumer_secret else 'None'}")
        
        url = f"{self.base_url}/api/Auth/RequestToken"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "consumer_key": self.consumer_key,
            "consumer_secret": self.consumer_secret
        }
        
        print(f"Making request to: {url}")
        print(f"Payload: {json.dumps(payload)}")
        
        try:
            response = requests.post(url, headers=headers, json=payload)  # Use json parameter instead of data
            print(f"Response status code: {response.status_code}")
            print(f"Response content: {response.text[:200]}...")  # Print first 200 chars to avoid overwhelming logs
            
            response.raise_for_status()
            data = response.json()
            
            if 'token' in data:
                self.auth_token = data.get('token')
                # Parse expiry date from response if available
                if 'expiryDate' in data:
                    try:
                        # Handle different datetime formats
                        expiry_str = data['expiryDate']
                        if '.' in expiry_str:
                            # Format with microseconds
                            expiry_str = expiry_str.split('.')[0] + 'Z'
                        self.token_expiry = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ")
                        print(f"Token will expire at: {self.token_expiry}")
                    except Exception as e:
                        # Fallback to 1 hour expiry
                        self.token_expiry = datetime.now().replace(hour=datetime.now().hour + 1)
                        print(f"Error parsing expiry date, using fallback: {str(e)}")
                else:
                    # Fallback to 1 hour expiry
                    self.token_expiry = datetime.now().replace(hour=datetime.now().hour + 1)
                    print(f"No expiry date in response, using fallback expiry")
                
                print(f"Successfully obtained auth token")
                return self.auth_token
            else:
                print(f"Error getting auth token: {data}")
                return None
        except Exception as e:
            print(f"Error getting auth token: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return None
    
    def register_ipn_url(self, ipn_url):
        """Register IPN URL with PesaPal"""
        print(f"Registering IPN URL: {ipn_url}")
        token = self.get_auth_token()
        if not token:
            print("Failed to get auth token, cannot register IPN URL")
            return None
        
        url = f"{self.base_url}/api/URLSetup/RegisterIPN"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        payload = {
            "url": ipn_url,
            "ipn_notification_type": "GET"
        }
        
        print(f"Request URL: {url}")
        print(f"Request payload: {json.dumps(payload)}")
        
        try:
            # Use json parameter instead of data for proper JSON serialization
            response = requests.post(url, headers=headers, json=payload)
            print(f"Response status code: {response.status_code}")
            print(f"Response content: {response.text[:200]}...")  # Print first 200 chars
            
            response.raise_for_status()
            data = response.json()
            
            # Check for ipn_id which is the most important field
            if 'ipn_id' in data:
                ipn_id = data.get('ipn_id')
                print(f"Successfully registered IPN URL with ID: {ipn_id}")
                return ipn_id
            else:
                print(f"Error registering IPN URL: {data}")
                return None
        except Exception as e:
            print(f"Error registering IPN URL: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return None
    
    def create_payment_order(self, amount, description, email, phone, first_name, last_name, callback_url, notification_id=None, currency="USD"):
        """Create payment order with PesaPal"""
        print(f"\n===== Creating Payment Order =====")
        print(f"Amount: {amount} {currency}")
        print(f"Description: {description}")
        print(f"Customer: {email}, {first_name} {last_name}")
        print(f"Callback URL: {callback_url}")
        
        token = self.get_auth_token()
        if not token:
            print("Failed to get auth token, cannot create payment order")
            return None
            
        # Register IPN URL first to get a valid notification_id
        print("\n===== Registering IPN URL =====")
        ipn_url = callback_url  # Use the same callback URL for IPN
        registered_ipn_id = self.register_ipn_url(ipn_url)
        
        if registered_ipn_id:
            print(f"Successfully registered IPN URL: {ipn_url}")
            print(f"New IPN ID: {registered_ipn_id}")
            # Use the newly registered IPN ID
            notification_id = registered_ipn_id
            # Update the instance IPN ID for future use
            self.ipn_id = registered_ipn_id
        else:
            print("Failed to register IPN URL, will try with existing IPN ID")
            # Continue with the existing IPN ID
        
        url = f"{self.base_url}/api/Transactions/SubmitOrderRequest"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        # Generate a unique ID for the transaction
        merchant_reference = f"ELIZA-{int(datetime.now().timestamp())}-{uuid.uuid4().hex[:8]}"
        print(f"Generated merchant reference: {merchant_reference}")
        
        # Prepare payload with proper currency
        payload = {
            "id": merchant_reference,
            "currency": currency,
            "amount": float(amount),
            "description": description,
            "callback_url": callback_url,
            "notification_id": notification_id or self.ipn_id,
            "branch": "Main",  # Required by PesaPal
            "billing_address": {
                "email_address": email,
                "phone_number": phone or "",
                "first_name": first_name,
                "last_name": last_name or "",
                "country_code": "UG",  # Default to Uganda
                "line_1": "Kampala",  # Required by PesaPal
                "city": "Kampala",  # Required by PesaPal
                "postal_code": ""  # Optional postal code
            }
        }
        
        print(f"Request URL: {url}")
        print(f"Request headers: {headers}")
        print(f"Request payload: {json.dumps(payload, indent=2)}")
        
        
        try:
            # Use json parameter instead of data for proper JSON serialization
            response = requests.post(url, headers=headers, json=payload)
            print(f"Response status code: {response.status_code}")
            print(f"Response content: {response.text[:500]}...")  # Print first 500 chars
            
            response.raise_for_status()
            data = response.json()
            
            # Check for redirect_url which is the most important field
            if 'redirect_url' in data:
                result = {
                    'merchant_reference': merchant_reference,
                    'redirect_url': data.get('redirect_url')
                }
                
                # Add order_tracking_id if available
                if 'order_tracking_id' in data:
                    result['order_tracking_id'] = data.get('order_tracking_id')
                
                print(f"Successfully created payment order")
                print(f"Redirect URL: {data.get('redirect_url')}")
                return result
            else:
                print(f"Error creating payment order: {data}")
                return None
        except Exception as e:
            print(f"Error creating payment order: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return None
    
    def get_transaction_status(self, order_tracking_id):
        """Get transaction status from PesaPal"""
        token = self.get_auth_token()
        if not token:
            return None
        
        url = f"{self.base_url}/api/Transactions/GetTransactionStatus?orderTrackingId={order_tracking_id}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') == '200':
                return {
                    'payment_status': data.get('payment_status_description'),
                    'payment_method': data.get('payment_method'),
                    'amount': data.get('amount'),
                    'created_date': data.get('created_date'),
                    'confirmation_code': data.get('confirmation_code')
                }
            else:
                print(f"Error getting transaction status: {data}")
                return None
        except Exception as e:
            print(f"Error getting transaction status: {str(e)}")
            return None
