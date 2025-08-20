import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

BFL_API_KEY = os.environ.get("BFL_API_KEY")
if not BFL_API_KEY:
    raise ValueError("BFL_API_KEY is not set")

request = requests.post(
    'https://api.bfl.ai/v1/flux-kontext-pro',
    headers={
        'accept': 'application/json',
        'x-key': os.environ.get("BFL_API_KEY"),
        'Content-Type': 'application/json',
    },
    json={
        'prompt': 'A logo of a genie lamp with picture of html carats on the lamp, abstract, professional',
        "aspect_ratio": "1:1"
    },
).json()

request_id = request["id"]
polling_url = request["polling_url"]

done = False
while not done:
    response = requests.get(polling_url).json()
    if response["status"] == "Ready":
        done = True
        response = requests.get(response["result"]["sample"])
        with open("output.png", "wb") as f:
            f.write(response.content)
    else:
        time.sleep(1)

{"model": "gpt-4o-mini", "messages": [{"role": "system", "content": "You are an expert frontend developer familiar with the latest frontend JS frameworks and tasked as a contractor to create SPAs with enterprise-grade professional designs. Make modern-looking pages with tasteful graphics, subtle animations, and modals where appropriate. Here is your task from the client:\n\nYou are WebPageGenie, an assistant that edits or creates single-file HTML5/CSS3/JS webpages. Prefer small, targeted edits to the existing page when possible. Preserve existing structure, styles, and links. Only replace or add the minimal necessary sections. If a full page is necessary, ensure it remains compatible with existing assets."}, {"role": "user", "content": "Task: can you make it so when the user selects a donation amount a modal appears to collect card information?\n\nCurrent page content (may be partial):\nThis is a demo template. Connect your payment processor to accept live donations.\n\nDonation Details Donation Frequency One-time Monthly Recurring monthly gifts provide sustained support and can be modified or canceled anytime. Select Amount Other amount (USD) Please enter a valid amount of $1.00 or more. Amount must be at least $1.00. Add estimated processing fees (2.9% + $0.30) Gift Amount Charged today Your Information First name Last name Email Phone (optional) Street address City State/Region ZIP/Postal code Country Select country United States Canada United Kingdom Australia Other Payment Details Secure 256-bit encryption Card number Expiry CVC Give Now Processing… By donating, you agree to our privacy policy .\n\nSecure Giving Donation Form Make a Gift Donation Details Donation Frequency One-time Monthly Recurring monthly gifts provide sustained support and can be modified or canceled anytime. Select Amount Other amount (USD) Please enter a valid amount of $1.00 or more. Amount must be at least $1.00. Add estimated processing fees (2.9% + $0.30) Gift Amount Charged today Your Information First name Last name Email Phone (optional) Street address City State/Region ZIP/Postal code Country Select country United States Canada United Kingdom Australia Other Payment Details Secure 256-bit encryption Card number Expiry CVC Give Now Processing… By donating, you agree to our privacy policy . Placeholder legal text: Donations may support the work of ACLU and/or the ACLU Foundation. Consult your tax advisor regarding deductibility. This page is a donation interface template. Replace placeholder images and copy as needed. Thank you! Your gift pledge has been recorded. This is a demo template. Connect your payment processor to accept live donations. Close\n\nDonation Details Donation Frequency One-time Monthly Recurring monthly gifts provide sustained support and can be modified or canceled anytime. Select Amount Other amount (USD) Please enter a valid amount of $1.00 or more. Amount must be at least $1.00. Add estimated processing fees (2.9% + $0.30) Gift Amount Charged today\n\nDonation Details Donation Frequency One-time Monthly Recurring monthly gifts provide sustained support and can be modified or canceled anytime. Select Amount Other amount (USD) Please enter a valid amount of $1.00 or more. Amount must be at least $1.00. Add estimated processing fees (2.9% + $0.30) Gift Amount Charged today\n\nOutput requirement:\n- Return a complete, valid SINGLE-FILE HTML document. ALL CSS and JS must be inline within the HTML. One file only.\n- Use Bootstrap 5 for styling (inline the CSS). You may inline visual libraries (Mermaid.js, Chart.js, icons/animations, etc.) to enhance visuals, but keep everything in this single HTML file."}]}