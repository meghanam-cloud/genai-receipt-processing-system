# genai-receipt-processing-system

This project is a fully serverless receipt processing system built on AWS.
When a receipt image is uploaded, the system automatically extracts text using OCR
and then applies Generative AI to produce a clean, human-readable summary and
structured JSON output.

The entire pipeline is event-driven and requires no manual execution.

## Project Overview

The goal of this project is to automate receipt understanding and expense extraction
using cloud-native and GenAI services.

Receipt images uploaded to Amazon S3 trigger a multi-stage processing pipeline:
first extracting structured data using AWS Textract, and then enriching and
normalizing the data using Amazon Bedrock with a large language model.

## Architecture Overview

**Flow:**

Amazon S3 → Lambda (Textract) → Amazon S3 → Lambda (Bedrock) → Amazon S3

**Key Components:**
- **Amazon S3** – Stores uploaded receipts and processed outputs
- **AWS Lambda (Textract Processor)** – Performs OCR and expense extraction
- **AWS Textract (AnalyzeExpense)** – Extracts structured receipt data
- **AWS Lambda (Bedrock Processor)** – Applies GenAI reasoning
- **Amazon Bedrock (Claude 3)** – Generates summaries and normalized JSON

## End-to-End Processing Flow

1. A receipt image is uploaded to the S3 `uploads/` folder.
2. The S3 upload event automatically triggers the first Lambda function.
3. Lambda uses AWS Textract (AnalyzeExpense) to extract receipt data.
4. Textract outputs are stored in S3 as both raw and cleaned JSON.
5. Creation of the cleaned summary JSON triggers the second Lambda function.
6. The second Lambda sends the extracted data to Amazon Bedrock.
7. Bedrock generates a natural-language summary and normalized JSON output.
8. Final AI-generated outputs are stored back in S3.

## S3 Folder Structure

receipt-processor-bucket/
│
├── uploads/
│   └── receipt.jpg
│
├── textract-output/
│   ├── receipt.jpg.textract.json
│   └── receipt.jpg.summary.json
│
└── bedrock-output/
    ├── receipt.jpg.summary.txt
    └── receipt.jpg.bedrock.json

## Screenshots
### S3 Bucket Structure
screenshots/s3-bucket-structure.png

### Textract Extraction Output
screenshots/textract-summary.png

### GenAI Output (Amazon Bedrock)
screenshots/bedrock-output.png



