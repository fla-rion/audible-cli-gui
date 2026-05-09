import audible
import httpx
import json
from pathlib import Path

AUTH_FILE = Path.home() / ".audible_tool" / "auth.json"

def test_exhaustive_license(asin):
    auth = audible.Authenticator.from_file(str(AUTH_FILE))
    headers = dict(auth.access_headers)
    
    # Try different User-Agents
    uas = [
        "Audible/3.56.2 (iPhone; iOS 15.1; Scale/3.00)",
        "Audible/1.46.1 (iPhone; iOS 15.1; Scale/3.00)",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Mobile/15E148 Safari/604.1"
    ]
    
    # Try different codecs and qualities
    combinations = [
        ("Extreme", "AAX_22_128"),
        ("High", "AAX_22_128"),
        ("Extreme", "aax_44_128"),
        ("High", "aax_44_128"),
        ("Normal", "aax_22_64")
    ]
    
    with httpx.Client(follow_redirects=True) as client:
        for ua in uas:
            for quality, codec in combinations:
                print(f"\nUA: {ua[:30]}... | Quality: {quality} | Codec: {codec}")
                h = headers.copy()
                h["User-Agent"] = ua
                url = f"https://api.audible.de/1.0/content/{asin}/licenserequest"
                params = {
                    "quality": quality,
                    "consumption_type": "Download",
                    "requested_codec": codec,
                    "supported_drm_types": "Adrm"
                }
                try:
                    resp = client.get(url, headers=h, params=params)
                    if resp.status_code == 200:
                        print("SUCCESS!")
                        # print(resp.json())
                        return
                    else:
                        print(f"Failed: {resp.status_code} | {resp.text[:50]}")
                except Exception as e:
                    print(f"Error: {e}")

if __name__ == "__main__":
    test_exhaustive_license("B0C7RDJPMN")
