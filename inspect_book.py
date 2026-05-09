import audible
import json
from pathlib import Path

AUTH_FILE = Path.home() / ".audible_tool" / "auth.json"

def inspect_book(asin):
    auth = audible.Authenticator.from_file(str(AUTH_FILE))
    with audible.Client(auth=auth) as client:
        print(f"--- Inspecting {asin} ---")
        
        # 1. Get Library Item with more groups
        try:
            item = client.get(
                f"1.0/library/{asin}",
                response_groups="product_desc,product_attrs,relationships,product_extended_attrs,series,media,content_reference,content_url"
            )
            print("Library Item Meta:", json.dumps(item, indent=2))
        except Exception as e:
            print(f"Library Fetch Failed: {e}")

        # 2. Try various licenserequest codecs
        codecs = ["AAX_22_128", "AAX_44_128", "AAX_44_64", "AAXC"]
        for codec in codecs:
            print(f"\nTrying codec: {codec}")
            try:
                if codec == "AAXC":
                    resp = client.post(
                        "1.0/content/licenserequest",
                        body={
                            "asins": [asin],
                            "quality": "Extreme",
                            "consumption_type": "Download",
                            "requested_codec": "AAXC",
                            "supported_drm_types": ["Adrm"],
                            "fulfill_drm": True,
                        },
                    )
                else:
                    resp = client.get(
                        f"1.0/content/{asin}/licenserequest",
                        quality="Extreme",
                        consumption_type="Download",
                        requested_codec=codec,
                        supported_drm_types="Adrm",
                    )
                print(f"Success with {codec}!")
                print(json.dumps(resp, indent=2))
                return
            except Exception as e:
                print(f"Failed {codec}: {e}")

if __name__ == "__main__":
    inspect_book("B0C7RDJPMN")
