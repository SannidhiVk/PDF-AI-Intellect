"""Query actual column names for document_shares."""
import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY", os.environ["SUPABASE_KEY"])
client = create_client(url, key)

test_cols = ["id", "document_id", "share_token", "created_by", "user_id", "owner_id", 
             "created_at", "is_active", "shared_by", "creator_id"]

print("Testing which columns exist in document_shares:")
for col in test_cols:
    try:
        resp = client.table("document_shares").select(col).limit(0).execute()
        print(f"  [YES] {col}")
    except Exception as e:
        err_msg = str(e)
        if "does not exist" in err_msg or "42703" in err_msg:
            print(f"  [NO]  {col}")
        else:
            print(f"  [ERR] {col} - {err_msg[:120]}")
