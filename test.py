import os
import requests
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
        'prompt': 'A cat on its back legs running like a human is holding a big silver fish with its arms. The cat is running away from the shop owner and has a panicked look on his face. The scene is situated in a crowded market.',
        "aspect_ratio": "1:1"
    },
).json()

print(request)
request_id = request["id"]
polling_url = request["polling_url"]
print(f"Request ID: {request_id}")
print(f"Polling URL: {polling_url}")