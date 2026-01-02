import json
import boto3
import os
import logging
import re

s3 = boto3.client("s3")
textract = boto3.client("textract")
logger = logging.getLogger()
logger.setLevel(logging.INFO)

OUTPUT_PREFIX = "textract-output/"

# ---------- Helper parser ----------
def parse_textract_expense(response):
    out = {"Vendor": None, "Total": None, "Date": None, "Items": []}
    docs = response.get("ExpenseDocuments", [])
    if docs:
        doc = docs[0]
        # SummaryFields: primary source for vendor/total/date
        for field in doc.get("SummaryFields", []):
            t = (field.get("Type", {}).get("Text") or "").strip().lower()
            v = (field.get("ValueDetection", {}).get("Text") or "").strip()
            if not v:
                v = (field.get("LabelDetection", {}).get("Text") or "").strip()
            if "vendor" in t or "merchant" in t or "merchant_name" in t:
                out["Vendor"] = v
            elif "total" in t or t == "amount":
                out["Total"] = v
            elif "date" in t:
                out["Date"] = v

        # Line items (if any)
        for grp in doc.get("LineItemGroups", []):
            for line in grp.get("LineItems", []):
                parts = []
                for kv in line.get("LineItemExpenseFields", []):
                    name = (kv.get("Type", {}).get("Text") or "").strip()
                    val = (kv.get("ValueDetection", {}).get("Text") or "").strip()
                    if name and val:
                        parts.append(f"{name}: {val}")
                    elif val:
                        parts.append(val)
                if parts:
                    out["Items"].append(" | ".join(parts))

    # Fallback: regex from entire response JSON text
    txt = json.dumps(response)
    if not out["Total"]:
        m = re.search(r'([₹$€]\s?\d{1,3}(?:[,\d{3}]*)(?:\.\d{2})?)', txt)
        if m:
            out["Total"] = m.group(1)
    if not out["Date"]:
        m = re.search(r'(\b\d{1,2}[\/\-\s]\d{1,2}[\/\-\s]\d{2,4}\b)', txt)
        if m:
            out["Date"] = m.group(1)
    return out

# ---------- Main handler ----------
def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))
    try:
        record = event["Records"][0]
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
    except Exception:
        logger.exception("Invalid event structure")
        raise

    logger.info("Processing S3 object: %s/%s", bucket, key)

    # Guard: skip any files in the output prefix (avoid recursion)
    if key.startswith(OUTPUT_PREFIX):
        logger.info("Skipping object in output prefix: %s", key)
        return {"statusCode": 200, "body": json.dumps({"message": "skipped - output file"})}

    # Only process likely receipt files
    lower = key.lower()
    if not (lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png") or lower.endswith(".pdf")):
        logger.info("Skipping non-image/pdf file: %s", key)
        return {"statusCode": 200, "body": json.dumps({"message": "skipped - not an image/pdf"})}

    # download to /tmp
    local_path = "/tmp/inputfile"
    try:
        s3.download_file(bucket, key, local_path)
    except Exception:
        logger.exception("Failed to download file from S3")
        raise

    # call Textract
    try:
        with open(local_path, "rb") as f:
            response = textract.analyze_expense(Document={'Bytes': f.read()})
    except Exception:
        logger.exception("Textract call failed")
        raise

    # save raw textract JSON
    key = os.path.basename(key)
    textract_key = os.path.join(OUTPUT_PREFIX, key + ".textract.json")
    s3.put_object(Bucket=bucket, Key=textract_key,
                  Body=json.dumps(response).encode("utf-8"))

    # parse & save summary
    try:
        summary = parse_textract_expense(response)
        summary["Source"] = key
    except Exception:
        logger.exception("Parsing Textract response failed")
        summary = {"Source": key, "Vendor": None, "Total": None, "Date": None, "Items": []}

    summary_key = os.path.join(OUTPUT_PREFIX, key + ".summary.json")
    s3.put_object(Bucket=bucket, Key=summary_key,
                  Body=json.dumps(summary, indent=2).encode("utf-8"))

    logger.info("✅ Saved outputs in s3://%s/%s*", bucket, OUTPUT_PREFIX)
    return {"statusCode": 200, "body": json.dumps(summary)}
