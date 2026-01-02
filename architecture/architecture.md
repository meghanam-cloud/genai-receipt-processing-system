# Architecture Overview

This project implements a fully serverless, event-driven GenAI pipeline for
processing receipt images.

The system is designed as a multi-stage pipeline where each stage has a clear,
single responsibility.

## High-Level Flow

Amazon S3 (Upload)
→ Lambda (Textract Processor)
→ Amazon S3 (Textract Outputs)
→ Lambda (Bedrock Processor)
→ Amazon S3 (Final AI Outputs)

## Stage 1: Receipt Ingestion

- Receipt images are uploaded to the S3 `uploads/` folder.
- The upload event automatically triggers the first Lambda function.
- No manual execution or API calls are required.

## Stage 2: OCR & Structured Extraction

- The first Lambda function uses AWS Textract (AnalyzeExpense).
- Textract extracts structured receipt information such as:
  - Vendor
  - Date
  - Line items
  - Total amount
- Outputs are stored in S3 as:
  - Raw Textract JSON (for auditing)
  - Cleaned summary JSON (for AI processing)

## Stage 3: GenAI Enrichment

- Creation of the summary JSON triggers the second Lambda function.
- This Lambda sends the extracted data to Amazon Bedrock.
- A Claude 3 model generates:
  - A human-readable summary
  - A normalized, analytics-ready JSON structure

## Design Principles

- **Event-driven**: Each stage is triggered automatically by S3 events
- **Separation of concerns**: OCR and GenAI logic are isolated
- **Auditability**: Raw and processed outputs are retained
- **Serverless**: No infrastructure to manage, scalable by design
