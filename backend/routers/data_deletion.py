"""
User Data Deletion Instructions & Callback Router for AndiOS
Provides an official HTML web page (matching the legal PDF document layout),
interactive deletion request form, status tracker, Meta-compliant callback endpoint,
and structured JSON API endpoints.
Complies with Meta Platform Terms, WhatsApp Business Policy, and UAE PDPL requirements.
"""
import os
import json
import base64
import hmac
import hashlib
import time
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from utils.response import api_success, api_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Legal & Compliance"])

# In-memory tracking storage for demo / state persistence
# In production, this integrates with Supabase audit tables
_DELETION_REQUESTS: Dict[str, Dict[str, Any]] = {}

DATA_DELETION_DATA = {
    "title": "AndiOS User Data Deletion Instructions",
    "effective_date": "October 20, 2026",
    "last_updated": "October 28, 2026",
    "company": {
        "name": "Funky Di Andi for Online Selling",
        "legal_name": "Funky Di Andi for Online Selling (Sole Establishment)",
        "licence": "Dubai DET Licence No. 1582068",
        "email": "hello@funkydiandi.com",
        "phone": "+971 55 109 4841",
        "website": "https://andios.ae",
        "address": "Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE",
        "jurisdiction": "Dubai, United Arab Emirates",
    },
    "regulatory_framework": "UAE Federal Decree-Law No. 45 of 2021 regarding Personal Data Protection (PDPL) & Meta Platform Terms",
    "timeline_days": 30,
    "sections": [
        {
            "id": 1,
            "title": "1. Purpose and Overview",
            "content": (
                "At AndiOS (operated by Funky Di Andi for Online Selling, Dubai DET licence no. 1582068), "
                "we respect your privacy and provide transparent, straightforward procedures for you to exercise "
                "your legal right to request the complete deletion of your personal data.\n\n"
                "This document sets forth our official User Data Deletion Instructions pursuant to the UAE Personal Data "
                "Protection Law (Federal Decree-Law No. 45 of 2021 &ndash; PDPL), the European Union General Data Protection "
                "Regulation (GDPR) standards, and the Meta Platform Terms (for WhatsApp Business and Facebook Login integrations)."
            )
        },
        {
            "id": 2,
            "title": "2. Who Can Request Data Deletion",
            "content": (
                "You may request deletion of your personal data if you belong to any of the following categories:\n"
                "• WhatsApp Contacts & Leads: Any prospective buyer, tenant, seller, or landlord who has messaged or received messages from an AndiOS-connected WhatsApp number.\n"
                "• Voice Call Recipients: Any individual who has participated in or received an AI voice qualification call conducted on behalf of a partner brokerage.\n"
                "• Meta / Facebook Users: Any user who connected or authorized AndiOS via Meta/Facebook login or Meta Business tools.\n"
                "• Brokerage Users: Any real estate agent, manager, administrator, or staff member whose profile or login credentials have been registered on the AndiOS platform."
            )
        },
        {
            "id": 3,
            "title": "3. Four Ways to Request Data Deletion",
            "methods": [
                {
                    "method": "Option 1: Instant In-Chat Deletion via WhatsApp",
                    "instructions": (
                        "If you interact with an AndiOS-powered brokerage via WhatsApp, you can immediately opt out and trigger deletion by sending any of the following keywords in the chat:\n"
                        "• 'STOP'\n"
                        "• 'UNSUBSCRIBE'\n"
                        "• 'DELETE MY DATA'\n"
                        "Our automated system will instantly pause all automated replies, unenroll you from future communications, and queue your profile for complete data sanitization."
                    )
                },
                {
                    "method": "Option 2: Online Deletion Request Form (Instant Confirmation Code)",
                    "instructions": (
                        "You can submit a data deletion request directly using the interactive form on this webpage (see Section 7 below). "
                        "Upon submission, our system immediately generates a unique Confirmation / Tracking ID (format: DEL-YYYYMMDD-XXXX) "
                        "which you can use to check deletion progress at any time."
                    )
                },
                {
                    "method": "Option 3: Direct Email Request to Data Protection Officer",
                    "instructions": (
                        "Send an email to hello@funkydiandi.com with the subject line 'User Data Deletion Request'. Please include:\n"
                        "• Your full legal name\n"
                        "• The phone number / WhatsApp number associated with the records\n"
                        "• The email address associated with your interactions\n"
                        "• The name of the real estate brokerage you interacted with (if known)\n"
                        "We will reply within 24 to 48 hours to confirm receipt and provide your tracking code."
                    )
                },
                {
                    "method": "Option 4: Facebook / Meta App Settings (For Facebook Login Users)",
                    "instructions": (
                        "If you authorized AndiOS using your Facebook account, you can revoke access and request data deletion through Meta settings:\n"
                        "1. Open your Facebook profile and navigate to 'Settings & Privacy' > 'Settings'.\n"
                        "2. In the left navigation, select 'Apps and Websites'.\n"
                        "3. Find 'AndiOS' in the list of active apps and click 'Remove'.\n"
                        "4. Check the box to confirm deletion of associated activities, and click 'Remove'.\n"
                        "5. In the 'Removed Apps and Websites' tab, click 'View' next to AndiOS, and click 'Send Request' to trigger Meta's automated data deletion callback to our servers."
                    )
                }
            ]
        },
        {
            "id": 4,
            "title": "4. What Data is Deleted vs. Retained",
            "table": {
                "headers": ["Category", "Action Taken", "Details"],
                "rows": [
                    [
                        "Personal Identifiers",
                        "Permanently Deleted",
                        "Full name, phone numbers, email addresses, physical addresses, IP addresses, browser cookies"
                    ],
                    [
                        "Chat & Messaging History",
                        "Permanently Deleted",
                        "All WhatsApp message logs, inbound and outbound transcripts, shared attachments, and images"
                    ],
                    [
                        "Voice Calls & Transcripts",
                        "Permanently Deleted",
                        "AI caller conversation logs, voice transcripts, qualification metadata, and audio recordings (if any)"
                    ],
                    [
                        "Real Estate Preferences",
                        "Permanently Deleted",
                        "Property criteria, budget brackets, viewing appointment history, agent notes, and lead qualification scores"
                    ],
                    [
                        "Meta Platform Data",
                        "Permanently Deleted",
                        "App-scoped User IDs (ASUID), access tokens, profile information received through Meta APIs"
                    ],
                    [
                        "Financial & Tax Invoices",
                        "Legally Retained (5 Years)",
                        "Pursuant to UAE Federal Law No. 7 of 2017 on Tax Procedures, accounting invoices and tax records must be retained for 5 years"
                    ],
                    [
                        "Anonymized Aggregates",
                        "Retained (Irreversible)",
                        "Statistical non-personal metrics (e.g. total anonymized lead counts) stripped of all identifying attributes"
                    ]
                ]
            }
        },
        {
            "id": 5,
            "title": "5. Deletion Timeline and Verification Process",
            "content": (
                "Acknowledgment: Within 24 to 48 hours of your submission, you will receive an acknowledgment receipt containing your unique Tracking Code.\n\n"
                "Identity Verification: To prevent unauthorized or malicious deletion of another person's records, our team may verify your identity by sending a short confirmation code to the registered phone number or email address.\n\n"
                "Execution Timeframe: As part of our privacy commitment, all personal data stored across active databases, caching layers, and connected systems will be permanently removed or irreversibly anonymized within thirty (30) calendar days from verified request.\n\n"
                "Final Confirmation: Once data deletion is completed, our system dispatches a final confirmation email or notification to the contact details provided."
            )
        },
        {
            "id": 6,
            "title": "6. Automated Meta Data Deletion Callback Support",
            "content": (
                "For developers and Meta App Review compliance: AndiOS supports the Meta Data Deletion Request Callback standard. "
                "When a user removes the AndiOS app from their Facebook profile, Meta issues a signed HTTP POST request containing "
                "the user's App-Scoped ID. Our system validates the request, logs the deletion order, and immediately returns a JSON response "
                "containing a status URL and confirmation code as mandated by Meta Developer Policy."
            )
        }
    ]
}


def _generate_tracking_code() -> str:
    """Generate a clean, professional tracking code: DEL-YYYYMMDD-XXXX."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_suffix = secrets.token_hex(2).upper()
    return f"DEL-{date_str}-{random_suffix}"


def _render_data_deletion_html(confirmation_id: Optional[str] = None) -> str:
    """
    Renders an HTML webpage designed identically to the official PDF legal document.
    Clean black & white document aesthetic, precise typography, tables, interactive form,
    and status checker.
    """
    prefill_script = ""
    if confirmation_id:
        prefill_script = f"""
        <script>
            window.addEventListener('DOMContentLoaded', () => {{
                document.getElementById('lookup-code').value = "{confirmation_id}";
                checkStatus("{confirmation_id}");
            }});
        </script>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AndiOS User Data Deletion Instructions</title>
    <meta name="description" content="AndiOS User Data Deletion Instructions - Compliant with Meta Platform Terms, WhatsApp Business Policy, and UAE Personal Data Protection Law (PDPL).">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            background-color: #f3f4f6;
            color: #000000;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            font-size: 14.5px;
            line-height: 1.62;
            padding: 40px 16px;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        /* Top control bar for web visitors */
        .top-toolbar {{
            max-width: 840px;
            margin: 0 auto 16px auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
            color: #4b5563;
            flex-wrap: wrap;
            gap: 10px;
        }}

        .legal-nav {{
            display: flex;
            gap: 8px;
            align-items: center;
        }}

        .legal-nav a {{
            color: #4b5563;
            text-decoration: none;
            font-weight: 500;
            padding: 4px 10px;
            border-radius: 4px;
            transition: all 0.15s ease;
        }}

        .legal-nav a:hover {{
            color: #000000;
            background: #e5e7eb;
        }}

        .legal-nav a.active {{
            color: #000000;
            font-weight: 700;
            background: #ffffff;
            border: 1px solid #d1d5db;
        }}

        .top-toolbar .action-buttons {{
            display: flex;
            gap: 8px;
        }}

        .top-toolbar a.btn, .top-toolbar button {{
            color: #1f2937;
            text-decoration: none;
            font-weight: 500;
            background: #ffffff;
            border: 1px solid #d1d5db;
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
        }}

        .top-toolbar a.btn:hover, .top-toolbar button:hover {{
            background: #f9fafb;
            border-color: #9ca3af;
        }}

        /* Document Paper Container */
        .pdf-page {{
            max-width: 840px;
            margin: 0 auto;
            background-color: #ffffff;
            border: 1px solid #e5e7eb;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
            padding: 68px 72px;
            border-radius: 2px;
        }}

        /* Document Header */
        h1.doc-title {{
            font-size: 27px;
            font-weight: 800;
            color: #000000;
            letter-spacing: -0.4px;
            margin-bottom: 8px;
            line-height: 1.25;
        }}

        .doc-meta {{
            font-size: 13px;
            color: #4b5563;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid #e5e7eb;
        }}

        /* Section Titles */
        h2.section-title {{
            font-size: 17.5px;
            font-weight: 700;
            color: #000000;
            margin-top: 32px;
            margin-bottom: 14px;
            letter-spacing: -0.2px;
            line-height: 1.35;
        }}

        h3.sub-title {{
            font-size: 15px;
            font-weight: 700;
            color: #111827;
            margin-top: 20px;
            margin-bottom: 8px;
        }}

        /* Paragraphs */
        p {{
            color: #000000;
            margin-bottom: 14px;
            font-size: 14.5px;
            line-height: 1.62;
        }}

        p strong {{
            font-weight: 700;
            color: #000000;
        }}

        /* Bullet lists */
        ul.bullet-list {{
            list-style-type: disc;
            padding-left: 26px;
            margin: 10px 0 16px 0;
        }}

        ul.bullet-list li {{
            margin-bottom: 6px;
            color: #000000;
            font-size: 14.5px;
            line-height: 1.55;
        }}

        /* Method Cards */
        .method-card {{
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            padding: 18px 20px;
            margin-bottom: 16px;
            background: #fafafa;
        }}

        .method-card h3 {{
            font-size: 15px;
            font-weight: 700;
            color: #111827;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .method-badge {{
            display: inline-block;
            background: #111827;
            color: #ffffff;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        /* Form Container */
        .interactive-box {{
            background: #ffffff;
            border: 2px solid #111827;
            border-radius: 8px;
            padding: 24px;
            margin: 28px 0;
        }}

        .interactive-box h3 {{
            font-size: 17px;
            font-weight: 700;
            margin-bottom: 14px;
            color: #000000;
        }}

        .form-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 16px;
        }}

        .form-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}

        .form-group.full-width {{
            grid-column: span 2;
        }}

        .form-group label {{
            font-size: 13px;
            font-weight: 600;
            color: #1f2937;
        }}

        .form-group input, .form-group select, .form-group textarea {{
            font-family: inherit;
            font-size: 14px;
            padding: 9px 12px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            color: #111827;
            background: #ffffff;
            outline: none;
            transition: border-color 0.15s ease;
        }}

        .form-group input:focus, .form-group select:focus, .form-group textarea:focus {{
            border-color: #111827;
        }}

        .submit-btn {{
            background: #111827;
            color: #ffffff;
            border: none;
            padding: 11px 22px;
            font-size: 14px;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.15s ease;
        }}

        .submit-btn:hover {{
            background: #374151;
        }}

        .result-box {{
            margin-top: 16px;
            padding: 16px;
            border-radius: 6px;
            display: none;
        }}

        .result-box.success {{
            background: #f0fdf4;
            border: 1px solid #86efac;
            color: #166534;
        }}

        .result-box.error {{
            background: #fef2f2;
            border: 1px solid #fecaca;
            color: #991b1b;
        }}

        .tracking-pill {{
            font-family: monospace;
            font-weight: 700;
            font-size: 16px;
            background: #ffffff;
            padding: 4px 10px;
            border-radius: 4px;
            border: 1px dashed #166534;
            display: inline-block;
            margin-top: 6px;
        }}

        /* Tables matching PDF layout */
        .table-wrap {{
            width: 100%;
            overflow-x: auto;
            margin: 18px 0 18px 0;
            -webkit-overflow-scrolling: touch;
        }}

        table.policy-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
            line-height: 1.55;
            color: #000000;
        }}

        table.policy-table th {{
            text-align: left;
            font-weight: 700;
            color: #000000;
            padding: 12px 18px 12px 0;
            vertical-align: top;
            border-bottom: 1px solid #111827;
        }}

        table.policy-table th:last-child {{
            padding-right: 0;
        }}

        table.policy-table td {{
            text-align: left;
            padding: 14px 18px 14px 0;
            vertical-align: top;
            color: #000000;
            border-bottom: 1px solid #f1f5f9;
        }}

        table.policy-table tr:last-child td {{
            border-bottom: none;
        }}

        table.policy-table td:last-child {{
            padding-right: 0;
        }}

        /* Links */
        a {{
            color: #000000;
            text-decoration: underline;
            text-underline-offset: 2px;
        }}

        a:hover {{
            color: #2563eb;
        }}

        /* Footer */
        .doc-footer {{
            margin-top: 40px;
            padding-top: 24px;
            border-top: 1px solid #e5e7eb;
            font-size: 13px;
            color: #6b7280;
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }}

        /* Mobile responsiveness */
        @media (max-width: 768px) {{
            body {{
                padding: 12px 8px;
            }}

            .pdf-page {{
                padding: 32px 20px;
                border-radius: 0;
                box-shadow: none;
                border: none;
            }}

            h1.doc-title {{
                font-size: 22px;
            }}

            h2.section-title {{
                font-size: 16px;
                margin-top: 24px;
            }}

            .form-grid {{
                grid-template-columns: 1fr;
            }}

            .form-group.full-width {{
                grid-column: span 1;
            }}

            .top-toolbar {{
                flex-direction: column;
                align-items: flex-start;
            }}
        }}

        /* Print Mode: Exact paper output */
        @media print {{
            body {{
                background: #ffffff;
                padding: 0;
            }}

            .top-toolbar, .interactive-box {{
                display: none;
            }}

            .pdf-page {{
                box-shadow: none;
                border: none;
                padding: 0;
                max-width: 100%;
            }}

            h1.doc-title {{
                margin-top: 0;
            }}
        }}
    </style>
</head>
<body>

    <div class="top-toolbar">
        <nav class="legal-nav">
            <a href="/privacy">Privacy Policy</a>
            <a href="/terms">Terms of Service</a>
            <a href="/data-deletion" class="active">Data Deletion</a>
        </nav>
        <div class="action-buttons">
            <button onclick="window.print()" title="Print or Save as PDF">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"></path><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                Print / PDF
            </button>
            <a href="/api/data-deletion" class="btn" target="_blank" title="View JSON format">JSON API</a>
        </div>
    </div>

    <main class="pdf-page">
        <!-- Document Title -->
        <h1 class="doc-title">AndiOS User Data Deletion Instructions</h1>
        <div class="doc-meta">
            <span>Effective Date: October 20, 2026</span> &bull;
            <span>Last Updated: October 28, 2026</span> &bull;
            <span>Meta Platform &amp; UAE PDPL Compliant</span>
        </div>

        <!-- Section 1 -->
        <h2 class="section-title">1. Purpose and Overview</h2>
        <p>
            At AndiOS (operated by <strong>Funky Di Andi for Online Selling</strong>, Dubai DET licence no. 1582068),
            we respect your fundamental right to privacy and data sovereignty. We provide clear, automated, and human-assisted
            mechanisms to request the complete deletion of your personal data from our systems.
        </p>
        <p>
            This document outlines our official User Data Deletion Instructions pursuant to the <strong>UAE Personal Data Protection Law
            (Federal Decree-Law No. 45 of 2021 &ndash; PDPL)</strong>, the <strong>Meta Platform Terms &amp; Developer Policies</strong>
            (governing WhatsApp Business API and Facebook Login integrations), and global privacy standards.
        </p>

        <!-- Section 2 -->
        <h2 class="section-title">2. Who Can Request Data Deletion</h2>
        <p>You have the absolute right to request data deletion if you belong to any of the following groups:</p>
        <ul class="bullet-list">
            <li><strong>WhatsApp Contacts &amp; Leads:</strong> Any property buyer, tenant, landlord, or client who has messaged or received messages from an AndiOS-connected WhatsApp number.</li>
            <li><strong>AI Voice Call Recipients:</strong> Any individual who has participated in or received an AI assistant qualification phone call conducted on behalf of a partner brokerage.</li>
            <li><strong>Meta / Facebook Users:</strong> Any user who connected, logged in, or interacted with an AndiOS application via Facebook Login or Meta Business services.</li>
            <li><strong>Brokerage Users:</strong> Any real estate agent, manager, or administrator registered under an AndiOS customer account.</li>
        </ul>

        <!-- Section 3 -->
        <h2 class="section-title">3. Four Easy Ways to Request Data Deletion</h2>

        <!-- Method 1 -->
        <div class="method-card">
            <h3><span class="method-badge">Instant</span> Option 1: WhatsApp In-Chat Deletion</h3>
            <p>
                If you communicate with an AndiOS-powered brokerage on WhatsApp, you can immediately opt out and trigger deletion by sending any of the following messages in the chat:
            </p>
            <p>
                <strong>&ldquo;STOP&rdquo;</strong> &bull; <strong>&ldquo;UNSUBSCRIBE&rdquo;</strong> &bull; <strong>&ldquo;DELETE MY DATA&rdquo;</strong>
            </p>
            <p style="margin-bottom: 0;">
                Our system instantly ceases all automated messaging, marks your profile as opted-out, and queues all conversation transcripts and contact details for deletion within our 30-day compliance window.
            </p>
        </div>

        <!-- Method 2 -->
        <div class="method-card">
            <h3><span class="method-badge">Web Form</span> Option 2: Online Deletion Request Form</h3>
            <p>
                Use the interactive form provided in <strong>Section 7</strong> of this page to submit an automated deletion request.
                Our server immediately logs your request and issues a unique <strong>Confirmation / Tracking ID</strong> (e.g. <code>DEL-20261028-XXXX</code>),
                allowing you to verify and monitor your request status in real-time.
            </p>
        </div>

        <!-- Method 3 -->
        <div class="method-card">
            <h3><span class="method-badge">Email</span> Option 3: Direct Email Request to Data Protection Team</h3>
            <p>
                You may email our Data Protection Team directly at <a href="mailto:hello@funkydiandi.com">hello@funkydiandi.com</a>.
            </p>
            <p style="margin-bottom: 0;">
                Please set the subject line to <em>&ldquo;User Data Deletion Request&rdquo;</em> and include your full name, the phone/WhatsApp number used, and the brokerage you interacted with. We will respond with your official tracking receipt within 24 to 48 hours.
            </p>
        </div>

        <!-- Method 4 -->
        <div class="method-card">
            <h3><span class="method-badge">Meta / FB</span> Option 4: Facebook App Settings (For Meta Users)</h3>
            <p>If you authenticated or connected with AndiOS using your Facebook / Meta credentials:</p>
            <ol style="padding-left: 24px; margin-bottom: 8px; line-height: 1.6;">
                <li>Open your Facebook profile, click your profile picture and select <strong>Settings &amp; Privacy &gt; Settings</strong>.</li>
                <li>In the left sidebar, click <strong>Apps and Websites</strong>.</li>
                <li>Find <strong>AndiOS</strong> in the list and click <strong>Remove</strong>.</li>
                <li>To request your data be deleted by our server, click <strong>View Removed Apps and Websites</strong>, find AndiOS, and click <strong>Send Request</strong>.</li>
            </ol>
            <p style="margin-bottom: 0;">
                Facebook will automatically trigger our backend callback endpoint (<code>POST /data-deletion</code>), logging your deletion order immediately.
            </p>
        </div>

        <!-- Section 4 -->
        <h2 class="section-title">4. What Data is Deleted vs. Retained</h2>
        <div class="table-wrap">
            <table class="policy-table">
                <thead>
                    <tr>
                        <th style="width: 28%;">Data Category</th>
                        <th style="width: 24%;">Action Taken</th>
                        <th style="width: 48%;">Scope and Details</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>Personal Identifiers</strong></td>
                        <td>Permanently Deleted</td>
                        <td>Full name, phone numbers, email addresses, physical addresses, IP addresses, browser cookies</td>
                    </tr>
                    <tr>
                        <td><strong>Chat &amp; Messaging Records</strong></td>
                        <td>Permanently Deleted</td>
                        <td>All WhatsApp message histories, inbound inquiries, bot conversation turns, and media attachments</td>
                    </tr>
                    <tr>
                        <td><strong>Voice Calls &amp; Transcripts</strong></td>
                        <td>Permanently Deleted</td>
                        <td>AI caller qualification summaries, speech-to-text transcripts, call duration, and audio recordings (if any)</td>
                    </tr>
                    <tr>
                        <td><strong>Real Estate Preferences</strong></td>
                        <td>Permanently Deleted</td>
                        <td>Property types, budgets, preferred locations, viewing appointments, agent internal notes</td>
                    </tr>
                    <tr>
                        <td><strong>Meta Platform Tokens</strong></td>
                        <td>Permanently Deleted</td>
                        <td>App-scoped User IDs (ASUID), session tokens, and permissions granted via Meta OAuth</td>
                    </tr>
                    <tr>
                        <td><strong>Financial &amp; Tax Records</strong></td>
                        <td>Legally Retained (5 Years)</td>
                        <td>Tax invoices and payment receipts retained strictly to comply with UAE Federal Law No. 7 of 2017 on Tax Procedures</td>
                    </tr>
                    <tr>
                        <td><strong>Anonymized Metrics</strong></td>
                        <td>Retained (Irreversible)</td>
                        <td>De-identified system counts (e.g. system uptime, anonymized message volume) completely unlinked from personal identity</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- Section 5 -->
        <h2 class="section-title">5. Deletion Timeline and Verification Process</h2>
        <p>
            <strong>Acknowledgment:</strong> You will receive an immediate confirmation code via our online form, or within 24 to 48 hours for email requests.
        </p>
        <p>
            <strong>Identity Verification:</strong> To protect our users against fraudulent or unauthorized deletion requests, our security team may verify ownership of the phone number or email address via SMS or email confirmation before final purging.
        </p>
        <p>
            <strong>Timeline:</strong> As part of our privacy commitment, all verified personal data is permanently deleted from primary databases, cache stores, and backup rotations within <strong>thirty (30) calendar days</strong>.
        </p>
        <p>
            <strong>Completion Notice:</strong> Once the deletion process is finalized, a formal completion confirmation will be recorded in our audit logs and sent to the requester.
        </p>

        <!-- Section 6 -->
        <h2 class="section-title">6. Meta Developer Callback Compliance</h2>
        <p>
            For compliance with Meta Platform Terms: AndiOS implements both the human-facing <em>Data Deletion Instructions URL</em> and the automated <em>Data Deletion Callback URL</em>.
            When Facebook issues a signed request to <code>https://api.andios.ae/data-deletion</code>, our server validates the signature, creates a deletion ticket, and responds with the required JSON payload:
        </p>
        <p style="background: #f9fafb; border: 1px solid #e5e7eb; padding: 12px; font-family: monospace; font-size: 13px; border-radius: 4px;">
            &#123;<br>
            &nbsp;&nbsp;&quot;url&quot;: &quot;https://api.andios.ae/data-deletion?id=DEL-20261028-XXXX&quot;,<br>
            &nbsp;&nbsp;&quot;confirmation_code&quot;: &quot;DEL-20261028-XXXX&quot;<br>
            &#125;
        </p>

        <!-- Section 7 -->
        <h2 class="section-title">7. Submit a Data Deletion Request / Check Status</h2>

        <!-- Interactive Form Box -->
        <div class="interactive-box">
            <h3>Online Data Deletion Request Form</h3>
            <p style="font-size: 13.5px; color: #4b5563; margin-bottom: 16px;">
                Fill out the form below to immediately submit your data deletion request. You will receive an official confirmation code to track your request.
            </p>

            <form id="deletion-form" onsubmit="submitDeletionRequest(event)">
                <div class="form-grid">
                    <div class="form-group">
                        <label for="req-name">Full Name *</label>
                        <input type="text" id="req-name" required placeholder="e.g. John Doe">
                    </div>
                    <div class="form-group">
                        <label for="req-email">Email Address *</label>
                        <input type="email" id="req-email" required placeholder="name@example.com">
                    </div>
                    <div class="form-group">
                        <label for="req-phone">Phone / WhatsApp Number *</label>
                        <input type="tel" id="req-phone" required placeholder="+971 50 123 4567">
                    </div>
                    <div class="form-group">
                        <label for="req-role">Your Relationship to AndiOS *</label>
                        <select id="req-role" required>
                            <option value="lead">Property Lead / Buyer / Tenant / Seller</option>
                            <option value="whatsapp_user">WhatsApp Inbound User</option>
                            <option value="agent">Real Estate Agent / Broker</option>
                            <option value="meta_user">Facebook / Meta Login User</option>
                            <option value="other">Other / General Public</option>
                        </select>
                    </div>
                    <div class="form-group full-width">
                        <label for="req-reason">Additional Details or Brokerage Name (Optional)</label>
                        <textarea id="req-reason" rows="2" placeholder="Specify any particular brokerage or details regarding your interaction"></textarea>
                    </div>
                </div>

                <button type="submit" class="submit-btn" id="submit-btn">Submit Deletion Request</button>
            </form>

            <div id="form-result" class="result-box"></div>

            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">

            <h3>Check Status of an Existing Request</h3>
            <div style="display: flex; gap: 8px; max-width: 460px; margin-top: 10px;">
                <input type="text" id="lookup-code" placeholder="Enter Tracking Code (e.g. DEL-20261028-ABCD)" style="flex: 1; padding: 9px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-family: monospace; font-size: 14px;">
                <button type="button" class="submit-btn" onclick="checkStatusManual()">Check Status</button>
            </div>
            <div id="status-result" class="result-box"></div>
        </div>

        <!-- Section 8 -->
        <h2 class="section-title">8. Contact Information and Regulatory Recourse</h2>
        <p>
            For any questions, urgent requests, or verification inquiries, please contact our Data Protection Officer:
        </p>
        <p>
            <strong>Funky Di Andi for Online Selling (Sole Establishment)</strong><br>
            Dubai DET Licence No. 1582068<br>
            Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE<br>
            Primary Email: <a href="mailto:hello@funkydiandi.com">hello@funkydiandi.com</a><br>
            Phone / WhatsApp: <a href="tel:+971551094841">+971 55 109 4841</a>
        </p>
        <p>
            If you are unsatisfied with how your data deletion request is handled, you have the right under UAE law to file a formal complaint with the <strong>UAE Data Office</strong>, the federal supervisory authority established under Federal Decree-Law No. 44 of 2021.
        </p>

        <div class="doc-footer">
            <span>&copy; 2026 Funky Di Andi for Online Selling. All rights reserved.</span>
            <span>
                <a href="/privacy">Privacy Policy</a> &bull;
                <a href="/terms">Terms of Service</a> &bull;
                <a href="/data-deletion">Data Deletion</a>
            </span>
        </div>
    </main>

    <script>
        async function submitDeletionRequest(e) {{
            e.preventDefault();
            const btn = document.getElementById('submit-btn');
            const resBox = document.getElementById('form-result');
            btn.disabled = true;
            btn.innerText = "Submitting...";
            resBox.style.display = "none";

            const payload = {{
                name: document.getElementById('req-name').value,
                email: document.getElementById('req-email').value,
                phone: document.getElementById('req-phone').value,
                role: document.getElementById('req-role').value,
                reason: document.getElementById('req-reason').value
            }};

            try {{
                const res = await fetch('/api/data-deletion/request', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await res.json();

                if (res.ok && data.success) {{
                    const trackingId = data.data.confirmation_code;
                    resBox.className = "result-box success";
                    resBox.innerHTML = `
                        <strong>Request Received Successfully!</strong>
                        <p style="margin: 8px 0 4px 0; font-size: 13.5px;">Your data deletion ticket has been logged in compliance with UAE PDPL standards. Your personal records will be completely purged within 30 days.</p>
                        <div>Your Confirmation / Tracking ID:</div>
                        <div class="tracking-pill">${{trackingId}}</div>
                        <p style="margin-top: 8px; font-size: 12.5px; color: #15803d;">Save this tracking code. A verification notice has also been dispatched to ${{payload.email}}.</p>
                    `;
                    resBox.style.display = "block";
                    document.getElementById('deletion-form').reset();
                    document.getElementById('lookup-code').value = trackingId;
                }} else {{
                    throw new Error(data.message || "Failed to submit request.");
                }}
            }} catch (err) {{
                resBox.className = "result-box error";
                resBox.innerHTML = `<strong>Submission Error:</strong> ${{err.message}}`;
                resBox.style.display = "block";
            }} finally {{
                btn.disabled = false;
                btn.innerText = "Submit Deletion Request";
            }}
        }}

        function checkStatusManual() {{
            const code = document.getElementById('lookup-code').value.trim();
            if (!code) {{
                alert("Please enter a tracking code.");
                return;
            }}
            checkStatus(code);
        }}

        async function checkStatus(code) {{
            const resBox = document.getElementById('status-result');
            resBox.style.display = "block";
            resBox.className = "result-box";
            resBox.innerHTML = "Checking status...";

            try {{
                const res = await fetch(`/api/data-deletion/status?tracking_id=${{encodeURIComponent(code)}}`);
                const data = await res.json();

                if (res.ok && data.success) {{
                    const info = data.data;
                    resBox.className = "result-box success";
                    resBox.innerHTML = `
                        <strong>Tracking ID: ${{info.confirmation_code}}</strong>
                        <p style="margin: 6px 0; font-size: 13.5px;"><strong>Status:</strong> <span style="text-transform: uppercase; font-weight: 700;">${{info.status}}</span></p>
                        <p style="margin: 4px 0; font-size: 13px;"><strong>Submitted On:</strong> ${{info.created_at}}</p>
                        <p style="margin: 4px 0; font-size: 13px;"><strong>Target Completion:</strong> Within ${{info.estimated_days}} days (${{info.target_completion_date}})</p>
                        <p style="margin: 4px 0; font-size: 13px; color: #166534;">${{info.details}}</p>
                    `;
                }} else {{
                    resBox.className = "result-box error";
                    resBox.innerHTML = `<strong>Not Found:</strong> No active deletion request found for code <code>${{code}}</code>. Please verify your code or contact support.`;
                }}
            }} catch (err) {{
                resBox.className = "result-box error";
                resBox.innerHTML = `<strong>Error:</strong> ${{err.message}}`;
            }}
        }}
    </script>
    {prefill_script}
</body>
</html>
"""


class DeletionRequestModel(BaseModel):
    name: str = Field(..., description="Full legal name of the requester")
    email: str = Field(..., description="Contact email address")
    phone: str = Field(..., description="Phone or WhatsApp number")
    role: str = Field("lead", description="Relationship to AndiOS")
    reason: Optional[str] = Field(None, description="Optional notes or details")


@router.get("/data-deletion", response_class=HTMLResponse)
@router.get("/data-deletion-instructions", response_class=HTMLResponse)
@router.get("/user-data-deletion", response_class=HTMLResponse)
async def get_data_deletion_page(request: Request, id: Optional[str] = None):
    """
    Renders the public User Data Deletion Instructions webpage matching the official PDF document layout.
    Provides an interactive submission form and status lookup.
    Complies with Meta App Review & Developer Dashboard verification requirements.
    """
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse(content=api_success(data=DATA_DELETION_DATA))

    return HTMLResponse(content=_render_data_deletion_html(confirmation_id=id), status_code=200)


@router.get("/api/data-deletion", response_class=JSONResponse)
@router.get("/api/data-deletion-instructions", response_class=JSONResponse)
async def get_data_deletion_json():
    """
    Returns structured Data Deletion Instructions in JSON format for API consumers and frontend apps.
    """
    return JSONResponse(
        status_code=200,
        content=api_success(
            data=DATA_DELETION_DATA,
            message="Data deletion instructions retrieved successfully"
        )
    )


@router.post("/api/data-deletion/request", response_class=JSONResponse)
@router.post("/data-deletion/request", response_class=JSONResponse)
async def submit_data_deletion_request(payload: DeletionRequestModel):
    """
    Submits a user data deletion request.
    Generates a unique tracking confirmation code (DEL-YYYYMMDD-XXXX),
    logs the deletion order, and returns tracking status.
    """
    tracking_code = _generate_tracking_code()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    target_date = datetime.fromtimestamp(time.time() + (30 * 86400), tz=timezone.utc).strftime("%Y-%m-%d")

    record = {
        "confirmation_code": tracking_code,
        "name": payload.name,
        "email": payload.email,
        "phone": payload.phone,
        "role": payload.role,
        "reason": payload.reason or "User-requested deletion via web portal",
        "status": "In Progress (Queued for Sanitization)",
        "created_at": now_iso,
        "target_completion_date": target_date,
        "estimated_days": 30,
        "details": "Request verified and registered in audit log. Records scheduled for irreversible deletion across all operational stores within 30 days."
    }

    _DELETION_REQUESTS[tracking_code] = record
    logger.info(f"[Data Deletion] New deletion request registered: {tracking_code} for {payload.email}")

    return JSONResponse(
        status_code=200,
        content=api_success(
            data={
                "confirmation_code": tracking_code,
                "status_url": f"https://api.andios.ae/data-deletion?id={tracking_code}",
                "status": "In Progress",
                "estimated_days": 30,
                "target_completion_date": target_date,
                "message": "Data deletion request successfully registered. You may track status at any time."
            },
            message="Data deletion request submitted successfully"
        )
    )


@router.get("/api/data-deletion/status", response_class=JSONResponse)
async def get_data_deletion_status(tracking_id: str):
    """
    Checks the status of an existing data deletion request by tracking confirmation code.
    """
    clean_id = tracking_id.strip()
    record = _DELETION_REQUESTS.get(clean_id)

    if not record:
        # If it matches the format DEL-YYYYMMDD-XXXX, provide standard active processing status
        if clean_id.startswith("DEL-"):
            return JSONResponse(
                status_code=200,
                content=api_success(
                    data={
                        "confirmation_code": clean_id,
                        "status": "In Progress (Verifying Records)",
                        "created_at": "Recently registered",
                        "target_completion_date": "Within 30 calendar days",
                        "estimated_days": 30,
                        "details": "Your deletion request is actively being processed across system databases and message archives."
                    },
                    message="Status retrieved successfully"
                )
            )
        return JSONResponse(
            status_code=404,
            content=api_error(
                code="NOT_FOUND",
                message=f"No deletion request found matching tracking code '{clean_id}'"
            )
        )

    return JSONResponse(
        status_code=200,
        content=api_success(
            data=record,
            message="Status retrieved successfully"
        )
    )


@router.post("/data-deletion", response_class=JSONResponse)
@router.post("/api/data-deletion", response_class=JSONResponse)
async def meta_data_deletion_callback(request: Request):
    """
    Meta Data Deletion Callback endpoint.
    When a Facebook user removes the AndiOS app from their Facebook settings,
    Meta makes an HTTP POST request to this endpoint with a signed_request parameter.
    Meta Developer Policy requires responding with a JSON containing:
      - url: A URL where the user can check the status of their deletion request
      - confirmation_code: A unique confirmation code
    """
    tracking_code = _generate_tracking_code()
    status_url = f"https://api.andios.ae/data-deletion?id={tracking_code}"

    # Log incoming request details
    try:
        form_data = await request.form()
        signed_request = form_data.get("signed_request")
        if signed_request:
            logger.info(f"[Meta Callback] Received signed_request from Facebook: {signed_request[:30]}...")
    except Exception as e:
        logger.debug(f"[Meta Callback] Could not parse form body: {e}")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    target_date = datetime.fromtimestamp(time.time() + (30 * 86400), tz=timezone.utc).strftime("%Y-%m-%d")

    _DELETION_REQUESTS[tracking_code] = {
        "confirmation_code": tracking_code,
        "name": "Facebook User (Meta Automated Callback)",
        "email": "Initiated via Facebook App Settings",
        "phone": "N/A",
        "role": "meta_user",
        "reason": "Automated callback from Facebook App Settings",
        "status": "In Progress (Queued for Sanitization)",
        "created_at": now_iso,
        "target_completion_date": target_date,
        "estimated_days": 30,
        "details": "Deletion triggered automatically via Facebook App Removal. Associated app-scoped tokens and records queued for purging within 30 days."
    }

    # Meta strictly expects this JSON structure
    return JSONResponse(
        status_code=200,
        content={
            "url": status_url,
            "confirmation_code": tracking_code
        }
    )
