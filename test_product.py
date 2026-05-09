import audible
import json
from pathlib import Path

AUTH_FILE = Path.home() / ".audible_tool" / "auth.json"

def test_product_api(asin):
    auth = audible.Authenticator.from_file(str(AUTH_FILE))
    with audible.Client(auth=auth) as client:
        print(f"--- Testing Product API for {asin} ---")
        try:
            resp = client.get(
                f"1.0/catalog/products/{asin}",
                response_groups="media,content_reference,content_url"
            )
            print("Product Response:", json.dumps(resp, indent=2))
        except Exception as e:
            print(f"Product API Failed: {e}")

if __name__ == "__main__":
    test_product_api("B0C7RDJPMN")
