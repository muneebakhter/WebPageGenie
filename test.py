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