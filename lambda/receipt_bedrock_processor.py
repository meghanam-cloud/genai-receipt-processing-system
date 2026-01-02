import json
import boto3
import logging
import os
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("RECEIPT_BUCKET")
TEXTRACT_PREFIX = "textract-output/"
BEDROCK_PREFIX = "bedrock-output/"

# Bedrock client (cross-region to us-east-1)
br = boto3.client("bedrock-runtime", region_name="us-east-1")

# Use Claude 3 Haiku (messages API)
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

s3 = boto3.client("s3")


def parse_amount(s):
    if not s:
        return None
    s = str(s).replace(",", "").strip()
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)', s.replace('₹', '').replace('$', '').replace('€', ''))
    if m:
        try:
            return float(m.group(1))
        except:
            return None
    return None


def make_prompt(summary_obj):
    vendor = summary_obj.get("Vendor") or ""
    total = summary_obj.get("Total") or ""
    date = summary_obj.get("Date") or ""
    items = summary_obj.get("Items") or []

    items_text = "\n".join(f"- {it}" for it in items) if items else "(none)"
    return f"""Human: You are an expert assistant that converts extracted receipt data into a friendly one-line summary and a normalized JSON object.
Here is the extracted data:

Vendor: {vendor}
Total: {total}
Date: {date}
Items:
{items_text}

Task:
1) Produce a one-line English summary (concise).
2) Produce ONLY valid JSON (no trailing text) with keys:
   - vendor (string),
   - date (ISO 8601 YYYY-MM-DD or empty string),
   - amount (number or null),
   - currency (string or empty),
   - items (array of strings),
   - category (short string classification like 'Groceries','Transport','Auto','Dining','Other').

Return first the one-line summary, then on a new line write exactly:
===JSON===
and then the JSON only.

Assistant:"""


def extract_currency(s):
    if not s:
        return ""
    if '₹' in s or 'INR' in s or 'Rs' in s:
        return "INR"
    if '$' in s:
        return "USD"
    if '€' in s:
        return "EUR"
    return ""


def call_bedrock_messages(prompt_text):
    """
    Use the Bedrock Messages API for Anthropic Claude 3 models.
    Returns the model's textual output (string).
    """
    messages_payload = {
        "anthropic_version": "bedrock-2023-05-31",  # REQUIRED for Anthropic Messages API
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text}
                ]
            }
        ],
        "max_tokens": 400,   # REQUIRED
        "temperature": 0.2   # optional: lower -> more deterministic
    }

    body = json.dumps(messages_payload)

    # Call Bedrock
    resp = br.invoke_model(
        modelId=MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json"
    )

    # resp["body"] can be a StreamingBody - normalize it to a string
    raw = resp.get("body")
    try:
        if hasattr(raw, "read"):
            raw_bytes = raw.read()
            if isinstance(raw_bytes, (bytes, bytearray)):
                raw = raw_bytes.decode("utf-8")
            else:
                raw = str(raw_bytes)
        elif isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        else:
            raw = str(raw)
    except Exception:
        # fallback: ensure raw is string
        raw = str(raw)

    # Try parse JSON; if parsing fails, return raw text
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw

    # Extract text depending on model response schema
    # 1) Newer Claude: parsed.get("output", {}).get("content", [])
    text_out = None
    output = parsed.get("output", {}).get("content", [])
    if isinstance(output, list):
        for c in output:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                text_out = c["text"]
                break

    # 2) Fallback: older "outputs" schema
    if not text_out:
        outputs = parsed.get("outputs", [])
        if isinstance(outputs, list):
            for out in outputs:
                if out.get("type") == "message":
                    for c in out.get("content", []):
                        if c.get("type") == "text" and c.get("text"):
                            text_out = c["text"]
                            break
                    if text_out:
                        break

    # 3) Last-resort: try to find any "text" field
    if not text_out:
        def find_text(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "text" and isinstance(v, str):
                        return v
                    res = find_text(v)
                    if res:
                        return res
            if isinstance(obj, list):
                for item in obj:
                    res = find_text(item)
                    if res:
                        return res
            return None
        text_out = find_text(parsed)

    return text_out if text_out else raw


def parse_bedrock_output(raw):
    """
    Expect raw to contain:
    <one-line summary>\n===JSON===\n{...}
    Extract and return (summary_text, parsed_json)
    """
    if not raw:
        return "", {}
    raw = str(raw)
    parts = raw.split("===JSON===")
    summary_text = parts[0].strip() if parts else ""
    json_part = parts[1].strip() if len(parts) > 1 else ""
    parsed_json = {}
    if json_part:
        try:
            parsed_json = json.loads(json_part)
        except Exception:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                try:
                    parsed_json = json.loads(m.group(0))
                except:
                    parsed_json = {}
    return summary_text, parsed_json


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))
    try:
        record = event["Records"][0]
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
    except Exception:
        logger.exception("Invalid event structure")
        raise

    logger.info("Triggered for S3 object: %s/%s", bucket, key)

    # Only process textract summary files
    if not key.startswith(TEXTRACT_PREFIX) or not key.endswith(".summary.json"):
        logger.info("Skipping non-summary or wrong prefix: %s", key)
        return {"statusCode": 200, "body": "skipped"}

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        summary_text = obj["Body"].read().decode("utf-8")
        summary_data = json.loads(summary_text)
    except Exception:
        logger.exception("Failed to load summary JSON from S3")
        raise

    prompt = make_prompt(summary_data)
    logger.info("Calling Bedrock model %s for key %s", MODEL_ID, key)

    try:
        raw_out = call_bedrock_messages(prompt)
        # safe length log
        logger.info("Raw Bedrock output length: %d", len(raw_out) if isinstance(raw_out, str) else 0)
        summary_line, summary_json = parse_bedrock_output(raw_out)
    except Exception:
        logger.exception("Bedrock call or parse failed")
        # write an error marker to S3 for debugging
        try:
            base_name = os.path.basename(key).replace(".summary.json", "")
            s3.put_object(
                Bucket=bucket,
                Key=f"{BEDROCK_PREFIX}{base_name}.error.txt",
                Body=b"bedrock_call_failed"
            )
        except Exception:
            logger.exception("failed to write error marker")
        raise

    # Normalize amount and currency
    summary_json["amount"] = parse_amount(summary_json.get("amount") or summary_data.get("Total"))
    summary_json["currency"] = summary_json.get("currency") or extract_currency(summary_data.get("Total", ""))

    base = os.path.basename(key).replace(".summary.json", "")
    try:
        s3.put_object(Bucket=bucket, Key=f"{BEDROCK_PREFIX}{base}.summary.txt", Body=(summary_line or "").encode("utf-8"))
        s3.put_object(Bucket=bucket, Key=f"{BEDROCK_PREFIX}{base}.bedrock.json", Body=json.dumps(summary_json, indent=2).encode("utf-8"))
        logger.info("✅ Saved Bedrock outputs for %s", key)
    except Exception:
        logger.exception("Failed to save Bedrock outputs to S3")
        try:
            err_body = ("ERROR saving outputs").encode("utf-8")
            s3.put_object(Bucket=bucket, Key=f"{BEDROCK_PREFIX}{base}.error.txt", Body=err_body)
        except Exception:
            logger.exception("Also failed to write error marker")
        raise

    return {"statusCode": 200, "body": "Success"}
