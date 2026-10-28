"""
Privacy Policy Router for AndiOS
Provides both an exact-replica HTML web page (matching the official PDF policy document)
and structured JSON API endpoints.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from utils.response import api_success

router = APIRouter(tags=["Legal & Compliance"])

PRIVACY_POLICY_DATA = {
    "title": "AndiOS Privacy Policy",
    "effective_date": "October 20, 2026",
    "last_updated": "October 20, 2026",
    "company": {
        "name": "Funky Di Andi for Online Selling",
        "legal_name": "Funky Di Andi for Online Selling (Sole Establishment)",
        "licence": "Dubai DET Licence No. 1582068",
        "email": "hello@funkydiandi.com",
        "phone": "+971 55 109 4841",
        "website": "https://andios.ae",
        "address": "Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE",
    },
    "sections": [
        {
            "id": 1,
            "title": "1. Who we are and what this policy covers",
            "content": (
                "AndiOS is an AI operating system for real estate brokerages in the UAE. It is operated by Funky Di Andi for Online Selling, "
                "a sole establishment registered in Dubai, UAE, under Department of Economy and Tourism licence no. 1582068, with its "
                "registered address at Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE (\"AndiOS\", \"we\", \"us\").\n\n"
                "This policy explains how we collect, use, share and protect personal data when you:\n"
                "• visit andios.ae or contact us through it\n"
                "• request a demo, sign up for early access or become a customer\n"
                "• use the AndiOS platform as a brokerage owner, manager or agent\n"
                "• interact with a brokerage that uses AndiOS, for example by messaging it on WhatsApp or receiving a call from its AI assistant\n\n"
                "Our two roles. For website visitors, prospects and our own customer accounts, we decide how your data is used, so we are the controller. "
                "For the leads, clients, owners and tenants that a brokerage manages inside AndiOS, the brokerage is the controller and we act as its processor: "
                "we handle that data only on the brokerage's instructions and under our customer agreement. Our customers own their client data. We do not sell it, "
                "and we do not use one brokerage's client data to market to anyone or to benefit another brokerage.\n\n"
                "If you are a brokerage's client and have a question about your data, contact that brokerage first. We will help them respond."
            )
        },
        {
            "id": 2,
            "title": "2. Information we collect",
            "table": {
                "headers": ["Who", "What we collect", "How we get it"],
                "rows": [
                    [
                        "Website visitors",
                        "IP address, browser and device type, time of visit",
                        "Automatically, in our hosting provider's server logs"
                    ],
                    [
                        "Prospects and demo requests",
                        "Name, email, phone or WhatsApp number, brokerage name, role, team size, messages you send us",
                        "Forms on andios.ae, email, WhatsApp, calls"
                    ],
                    [
                        "Customer account users (owners, managers, agents)",
                        "Name, work email, phone, role, RERA or BRN number if provided, login details, billing contact, usage and activity logs, support conversations",
                        "You or your brokerage when the account is set up, and your use of the platform"
                    ],
                    [
                        "Brokerage clients and leads (processed for our customers)",
                        "Name, phone, email, WhatsApp messages, property interests and requirements, budget, viewing appointments, notes added by agents, listing and transaction details",
                        "The brokerage, connected channels (WhatsApp, phone, calendar), and the client's own messages"
                    ],
                    [
                        "Payment information",
                        "Billing name and address, invoice history",
                        "Our payment provider handles card details; we do not store full card numbers"
                    ]
                ]
            },
            "post_table_content": (
                "We do not ask for sensitive personal data such as health, religion or biometric data. If a brokerage uploads "
                "documents that contain it (for example a passport or Emirates ID for a tenancy contract), we process them only "
                "as that brokerage's processor and only for that purpose."
            )
        },
        {
            "id": 3,
            "title": "3. How we use information and why",
            "table": {
                "headers": ["Purpose", "Data used", "Legal basis under the PDPL"],
                "rows": [
                    [
                        "Run the website and answer enquiries",
                        "Visitor and prospect data",
                        "Your consent, or steps you asked us to take before a contract"
                    ],
                    [
                        "Provide the platform: lead capture, WhatsApp replies, AI calling, scheduling, CRM",
                        "Account and client data",
                        "Performing our contract with the brokerage; as processor, on its instructions"
                    ],
                    [
                        "Billing, account management and support",
                        "Account and payment data",
                        "Performing our contract"
                    ],
                    [
                        "Security, fraud prevention and fixing errors",
                        "Logs and usage data",
                        "Our legal obligations and protecting the service"
                    ],
                    [
                        "Improve AndiOS using aggregated, de-identified usage statistics",
                        "Usage data with names and contact details removed",
                        "Consent where required; never identifiable client data"
                    ],
                    [
                        "Send product updates and marketing to prospects and customers",
                        "Name, email, phone",
                        "Your consent. You can opt out at any time"
                    ],
                    [
                        "Meet legal, tax and regulatory requirements",
                        "What the law requires",
                        "Legal obligation"
                    ]
                ]
            },
            "post_table_content": (
                "We will not use your data for a new purpose that is unrelated to these without telling you first and, "
                "where required, asking for your consent."
            )
        },
        {
            "id": 4,
            "title": "4. AI, WhatsApp and AI calling",
            "content": (
                "AI processing. AndiOS uses large language models from OpenAI to draft replies, qualify leads, summarise "
                "conversations and schedule viewings. Message content and relevant lead details are sent to OpenAI only to "
                "produce a response. Under OpenAI's API terms, it does not use this data to train its models. We do not train "
                "our own AI models on identifiable client data.\n\n"
                "Human oversight. AI outputs support agents; they do not replace them. Brokerages decide which actions run "
                "automatically and can review, edit or stop AI replies. AndiOS does not make decisions that produce legal "
                "effects for a person, such as approving or refusing a tenancy, without a human at the brokerage.\n\n"
                "WhatsApp. Messages are sent and received through Meta's WhatsApp Business Platform. Meta processes message "
                "data under its own terms. Brokerages are responsible for having permission to message their contacts, and "
                "anyone can reply STOP to opt out.\n\n"
                "AI Caller. When a brokerage uses the AI Caller, the person called is told at the start that they are speaking "
                "with an AI assistant on behalf of that brokerage. Calls are not currently recorded. If we add recording, we "
                "will update this policy and tell people before any recording starts. A person can ask to speak to a human or "
                "end the call at any time."
            )
        },
        {
            "id": 5,
            "title": "5. Who we share data with",
            "pre_table_content": (
                "We share personal data only with the service providers we need to run AndiOS, under contracts that require "
                "them to protect it and use it only for our service:"
            ),
            "table": {
                "headers": ["Provider", "What they do", "Location"],
                "rows": [
                    ["Supabase", "Database and file storage", "UAE"],
                    ["OpenAI", "AI language models", "United States"],
                    ["Meta (WhatsApp Business Platform)", "WhatsApp messaging", "Outside the UAE"],
                    ["Google", "Calendar sync for viewings, when a user connects it", "Global"]
                ]
            },
            "post_table_content": (
                "We may also disclose data when UAE law, a court or a regulator requires it, or as part of a merger or "
                "sale of our business, with the buyer bound by this policy. We never sell personal data.\n\n"
                "International transfers. Some providers process data outside the UAE. When that happens, we transfer it only "
                "as the PDPL allows: to countries with adequate protection, or under contractual safeguards with the recipient. "
                "Ask us for details of the safeguards for any provider."
            )
        },
        {
            "id": 6,
            "title": "6. How long we keep data",
            "table": {
                "headers": ["Data", "Kept for"],
                "rows": [
                    ["Prospect and demo enquiries", "[24 months] from last contact, unless you become a customer"],
                    ["Customer account data", "Life of the contract plus [90 days], then deleted"],
                    ["Brokerage client data", "As the brokerage instructs; exported or deleted within [30 days] of contract end"],
                    ["Invoices and billing records", "As long as UAE tax and commercial law requires [5 years]"],
                    ["Security logs", "[12 months]"]
                ]
            }
        },
        {
            "id": 7,
            "title": "7. How we protect data",
            "content": (
                "Data is encrypted in transit and at rest. Access is limited by role, so agents see only what their brokerage "
                "allows. We use multi-factor authentication for admin access, keep access logs and review our providers' "
                "security. If a breach affects your personal data, we will notify you, our customers and the UAE Data Office "
                "as the law requires."
            )
        },
        {
            "id": 8,
            "title": "8. Your rights",
            "content": (
                "Under the PDPL you can ask to:\n\n"
                "• access the personal data we hold about you\n"
                "• correct inaccurate data\n"
                "• delete your data, unless we must keep it by law\n"
                "• restrict or object to processing, including direct marketing\n"
                "• receive your data in a portable format\n"
                "• withdraw consent at any time, without affecting earlier processing\n\n"
                "Email hello@funkydiandi.com or call or WhatsApp +971 55 109 4841. We will reply within [30 days] and may "
                "ask you to confirm your identity. If your data is held for a brokerage, we will pass your request to them. "
                "If you are not satisfied, you can complain to the UAE Data Office."
            )
        },
        {
            "id": 9,
            "title": "9. Cookies",
            "content": (
                "andios.ae does not currently use analytics, advertising or tracking cookies. It may set strictly necessary "
                "cookies the site needs to work, such as keeping you signed in. If we add analytics or other cookies, we will "
                "update this policy and ask for your consent first."
            )
        },
        {
            "id": 10,
            "title": "10. Children",
            "content": (
                "AndiOS is a business service. It is not meant for anyone under 18, and we do not knowingly collect their data."
            )
        },
        {
            "id": 11,
            "title": "11. Changes to this policy",
            "content": (
                "We will post updates on this page and change the date at the top. For significant changes, we will email "
                "customers before they take effect."
            )
        },
        {
            "id": 12,
            "title": "12. Contact us",
            "content": (
                "Funky Di Andi for Online Selling (Sole Establishment) Dubai licence no. 1582068 Villa 103, New Dubai, "
                "P.O. Box 12345, Dubai, UAE Email: hello@funkydiandi.com Phone / WhatsApp: +971 55 109 4841"
            )
        }
    ]
}


def _render_privacy_html() -> str:
    """
    Renders an HTML webpage designed identically to the official PDF document.
    Clean black & white document aesthetic, precise typography, tables, and spacing.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AndiOS Privacy Policy</title>
    <meta name="description" content="AndiOS Privacy Policy - AI operating system for real estate brokerages in the UAE. Operated by Funky Di Andi for Online Selling.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background-color: #f3f4f6;
            color: #000000;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            font-size: 14.5px;
            line-height: 1.62;
            padding: 40px 16px;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        /* Top control bar for web visitors */
        .top-toolbar {
            max-width: 840px;
            margin: 0 auto 16px auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
            color: #4b5563;
            flex-wrap: wrap;
            gap: 10px;
        }

        .legal-nav {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .legal-nav a {
            color: #4b5563;
            text-decoration: none;
            font-weight: 500;
            padding: 4px 10px;
            border-radius: 4px;
            transition: all 0.15s ease;
        }

        .legal-nav a:hover {
            color: #000000;
            background: #e5e7eb;
        }

        .legal-nav a.active {
            color: #000000;
            font-weight: 700;
            background: #ffffff;
            border: 1px solid #d1d5db;
        }

        .top-toolbar .action-buttons {
            display: flex;
            gap: 8px;
        }

        .top-toolbar a.btn, .top-toolbar button {
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
        }

        .top-toolbar a.btn:hover, .top-toolbar button:hover {
            background: #f9fafb;
            border-color: #9ca3af;
        }

        /* Document Paper Container */
        .pdf-page {
            max-width: 840px;
            margin: 0 auto;
            background-color: #ffffff;
            border: 1px solid #e5e7eb;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
            padding: 68px 72px;
            border-radius: 2px;
        }

        /* Document Header */
        h1.doc-title {
            font-size: 27px;
            font-weight: 800;
            color: #000000;
            letter-spacing: -0.4px;
            margin-bottom: 24px;
            line-height: 1.25;
        }

        /* Section Titles */
        h2.section-title {
            font-size: 17.5px;
            font-weight: 700;
            color: #000000;
            margin-top: 32px;
            margin-bottom: 14px;
            letter-spacing: -0.2px;
            line-height: 1.35;
        }

        /* Paragraphs */
        p {
            color: #000000;
            margin-bottom: 14px;
            font-size: 14.5px;
            line-height: 1.62;
        }

        p strong {
            font-weight: 700;
            color: #000000;
        }

        /* Bullet lists */
        ul.bullet-list {
            list-style-type: disc;
            padding-left: 26px;
            margin: 10px 0 16px 0;
        }

        ul.bullet-list li {
            margin-bottom: 5px;
            color: #000000;
            font-size: 14.5px;
            line-height: 1.55;
        }

        /* Tables matching PDF layout */
        .table-wrap {
            width: 100%;
            overflow-x: auto;
            margin: 18px 0 18px 0;
            -webkit-overflow-scrolling: touch;
        }

        table.policy-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
            line-height: 1.55;
            color: #000000;
        }

        table.policy-table th {
            text-align: left;
            font-weight: 700;
            color: #000000;
            padding: 12px 18px 12px 0;
            vertical-align: top;
            border-bottom: 1px solid #111827;
        }

        table.policy-table th:last-child {
            padding-right: 0;
        }

        table.policy-table td {
            text-align: left;
            padding: 14px 18px 14px 0;
            vertical-align: top;
            color: #000000;
            border-bottom: 1px solid #f1f5f9;
        }

        table.policy-table tr:last-child td {
            border-bottom: none;
        }

        table.policy-table td:last-child {
            padding-right: 0;
        }

        table.policy-table td strong {
            font-weight: 600;
        }

        /* Links */
        a {
            color: #000000;
            text-decoration: underline;
            text-underline-offset: 2px;
        }

        a:hover {
            color: #2563eb;
        }

        /* Mobile responsiveness */
        @media (max-width: 768px) {
            body {
                padding: 12px 8px;
            }

            .pdf-page {
                padding: 32px 20px;
                border-radius: 0;
                box-shadow: none;
                border: none;
            }

            h1.doc-title {
                font-size: 22px;
            }

            h2.section-title {
                font-size: 16px;
                margin-top: 24px;
            }

            table.policy-table th, table.policy-table td {
                padding: 10px 10px 10px 0;
                font-size: 13px;
            }
        }

        /* Print Mode: Exact paper output */
        @media print {
            body {
                background: #ffffff;
                padding: 0;
            }

            .top-toolbar {
                display: none;
            }

            .pdf-page {
                box-shadow: none;
                border: none;
                padding: 0;
                max-width: 100%;
            }

            h1.doc-title {
                margin-top: 0;
            }
        }
    </style>
</head>
<body>

    <div class="top-toolbar">
        <nav class="legal-nav">
            <a href="/privacy" class="active">Privacy Policy</a>
            <a href="/terms">Terms of Service</a>
            <a href="/data-deletion">Data Deletion</a>
        </nav>
        <div class="action-buttons">
            <button onclick="window.print()" title="Print or Save as PDF">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"></path><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
                Print / PDF
            </button>
            <a href="/api/privacy" class="btn" target="_blank" title="View JSON format">JSON API</a>
        </div>
    </div>

    <main class="pdf-page">
        <!-- Document Title -->
        <h1 class="doc-title">AndiOS Privacy Policy</h1>

        <!-- Section 1 -->
        <h2 class="section-title">1. Who we are and what this policy covers</h2>
        <p>
            AndiOS is an AI operating system for real estate brokerages in the UAE. It is operated by Funky Di Andi for Online Selling, a sole establishment registered in Dubai, UAE, under Department of Economy and Tourism licence no. 1582068, with its registered address at Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE (&ldquo;AndiOS&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;).
        </p>
        <p>This policy explains how we collect, use, share and protect personal data when you:</p>
        <ul class="bullet-list">
            <li>visit andios.ae or contact us through it</li>
            <li>request a demo, sign up for early access or become a customer</li>
            <li>use the AndiOS platform as a brokerage owner, manager or agent</li>
            <li>interact with a brokerage that uses AndiOS, for example by messaging it on WhatsApp or receiving a call from its AI assistant</li>
        </ul>
        <p>
            <strong>Our two roles.</strong> For website visitors, prospects and our own customer accounts, we decide how your data is used, so we are the <strong>controller</strong>. For the leads, clients, owners and tenants that a brokerage manages inside AndiOS, the brokerage is the controller and we act as its <strong>processor</strong>: we handle that data only on the brokerage's instructions and under our customer agreement. Our customers own their client data. We do not sell it, and we do not use one brokerage's client data to market to anyone or to benefit another brokerage.
        </p>
        <p>
            If you are a brokerage's client and have a question about your data, contact that brokerage first. We will help them respond.
        </p>

        <!-- Section 2 -->
        <h2 class="section-title">2. Information we collect</h2>
        <div class="table-wrap">
            <table class="policy-table">
                <thead>
                    <tr>
                        <th style="width: 26%;">Who</th>
                        <th style="width: 44%;">What we collect</th>
                        <th style="width: 30%;">How we get it</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Website visitors</td>
                        <td>IP address, browser and device type, time of visit</td>
                        <td>Automatically, in our hosting provider's server logs</td>
                    </tr>
                    <tr>
                        <td>Prospects and demo requests</td>
                        <td>Name, email, phone or WhatsApp number, brokerage name, role, team size, messages you send us</td>
                        <td>Forms on andios.ae, email, WhatsApp, calls</td>
                    </tr>
                    <tr>
                        <td>Customer account users (owners, managers, agents)</td>
                        <td>Name, work email, phone, role, RERA or BRN number if provided, login details, billing contact, usage and activity logs, support conversations</td>
                        <td>You or your brokerage when the account is set up, and your use of the platform</td>
                    </tr>
                    <tr>
                        <td>Brokerage clients and leads (processed for our customers)</td>
                        <td>Name, phone, email, WhatsApp messages, property interests and requirements, budget, viewing appointments, notes added by agents, listing and transaction details</td>
                        <td>The brokerage, connected channels (WhatsApp, phone, calendar), and the client's own messages</td>
                    </tr>
                    <tr>
                        <td>Payment information</td>
                        <td>Billing name and address, invoice history</td>
                        <td>Our payment provider handles card details; we do not store full card numbers</td>
                    </tr>
                </tbody>
            </table>
        </div>
        <p>
            We do not ask for sensitive personal data such as health, religion or biometric data. If a brokerage uploads documents that contain it (for example a passport or Emirates ID for a tenancy contract), we process them only as that brokerage's processor and only for that purpose.
        </p>

        <!-- Section 3 -->
        <h2 class="section-title">3. How we use information and why</h2>
        <div class="table-wrap">
            <table class="policy-table">
                <thead>
                    <tr>
                        <th style="width: 40%;">Purpose</th>
                        <th style="width: 30%;">Data used</th>
                        <th style="width: 30%;">Legal basis under the PDPL</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Run the website and answer enquiries</td>
                        <td>Visitor and prospect data</td>
                        <td>Your consent, or steps you asked us to take before a contract</td>
                    </tr>
                    <tr>
                        <td>Provide the platform: lead capture, WhatsApp replies, AI calling, scheduling, CRM</td>
                        <td>Account and client data</td>
                        <td>Performing our contract with the brokerage; as processor, on its instructions</td>
                    </tr>
                    <tr>
                        <td>Billing, account management and support</td>
                        <td>Account and payment data</td>
                        <td>Performing our contract</td>
                    </tr>
                    <tr>
                        <td>Security, fraud prevention and fixing errors</td>
                        <td>Logs and usage data</td>
                        <td>Our legal obligations and protecting the service</td>
                    </tr>
                    <tr>
                        <td>Improve AndiOS using aggregated, de-identified usage statistics</td>
                        <td>Usage data with names and contact details removed</td>
                        <td>Consent where required; never identifiable client data</td>
                    </tr>
                    <tr>
                        <td>Send product updates and marketing to prospects and customers</td>
                        <td>Name, email, phone</td>
                        <td>Your consent. You can opt out at any time</td>
                    </tr>
                    <tr>
                        <td>Meet legal, tax and regulatory requirements</td>
                        <td>What the law requires</td>
                        <td>Legal obligation</td>
                    </tr>
                </tbody>
            </table>
        </div>
        <p>
            We will not use your data for a new purpose that is unrelated to these without telling you first and, where required, asking for your consent.
        </p>

        <!-- Section 4 -->
        <h2 class="section-title">4. AI, WhatsApp and AI calling</h2>
        <p>
            <strong>AI processing.</strong> AndiOS uses large language models from OpenAI to draft replies, qualify leads, summarise conversations and schedule viewings. Message content and relevant lead details are sent to OpenAI only to produce a response. Under OpenAI's API terms, it does not use this data to train its models. We do not train our own AI models on identifiable client data.
        </p>
        <p>
            <strong>Human oversight.</strong> AI outputs support agents; they do not replace them. Brokerages decide which actions run automatically and can review, edit or stop AI replies. AndiOS does not make decisions that produce legal effects for a person, such as approving or refusing a tenancy, without a human at the brokerage.
        </p>
        <p>
            <strong>WhatsApp.</strong> Messages are sent and received through Meta's WhatsApp Business Platform. Meta processes message data under its own terms. Brokerages are responsible for having permission to message their contacts, and anyone can reply STOP to opt out.
        </p>
        <p>
            <strong>AI Caller.</strong> When a brokerage uses the AI Caller, the person called is told at the start that they are speaking with an AI assistant on behalf of that brokerage. Calls are not currently recorded. If we add recording, we will update this policy and tell people before any recording starts. A person can ask to speak to a human or end the call at any time.
        </p>

        <!-- Section 5 -->
        <h2 class="section-title">5. Who we share data with</h2>
        <p>
            We share personal data only with the service providers we need to run AndiOS, under contracts that require them to protect it and use it only for our service:
        </p>
        <div class="table-wrap">
            <table class="policy-table">
                <thead>
                    <tr>
                        <th style="width: 32%;">Provider</th>
                        <th style="width: 48%;">What they do</th>
                        <th style="width: 20%;">Location</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Supabase</td>
                        <td>Database and file storage</td>
                        <td>UAE</td>
                    </tr>
                    <tr>
                        <td>OpenAI</td>
                        <td>AI language models</td>
                        <td>United States</td>
                    </tr>
                    <tr>
                        <td>Meta (WhatsApp Business Platform)</td>
                        <td>WhatsApp messaging</td>
                        <td>Outside the UAE</td>
                    </tr>
                    <tr>
                        <td>Google</td>
                        <td>Calendar sync for viewings, when a user connects it</td>
                        <td>Global</td>
                    </tr>
                </tbody>
            </table>
        </div>
        <p>
            We may also disclose data when UAE law, a court or a regulator requires it, or as part of a merger or sale of our business, with the buyer bound by this policy. We never sell personal data.
        </p>
        <p>
            <strong>International transfers.</strong> Some providers process data outside the UAE. When that happens, we transfer it only as the PDPL allows: to countries with adequate protection, or under contractual safeguards with the recipient. Ask us for details of the safeguards for any provider.
        </p>

        <!-- Section 6 -->
        <h2 class="section-title">6. How long we keep data</h2>
        <div class="table-wrap">
            <table class="policy-table">
                <thead>
                    <tr>
                        <th style="width: 38%;">Data</th>
                        <th style="width: 62%;">Kept for</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Prospect and demo enquiries</td>
                        <td>[24 months] from last contact, unless you become a customer</td>
                    </tr>
                    <tr>
                        <td>Customer account data</td>
                        <td>Life of the contract plus [90 days], then deleted</td>
                    </tr>
                    <tr>
                        <td>Brokerage client data</td>
                        <td>As the brokerage instructs; exported or deleted within [30 days] of contract end</td>
                    </tr>
                    <tr>
                        <td>Invoices and billing records</td>
                        <td>As long as UAE tax and commercial law requires [5 years]</td>
                    </tr>
                    <tr>
                        <td>Security logs</td>
                        <td>[12 months]</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- Section 7 -->
        <h2 class="section-title">7. How we protect data</h2>
        <p>
            Data is encrypted in transit and at rest. Access is limited by role, so agents see only what their brokerage allows. We use multi-factor authentication for admin access, keep access logs and review our providers' security. If a breach affects your personal data, we will notify you, our customers and the UAE Data Office as the law requires.
        </p>

        <!-- Section 8 -->
        <h2 class="section-title">8. Your rights</h2>
        <p>Under the PDPL you can ask to:</p>
        <ul class="bullet-list">
            <li>access the personal data we hold about you</li>
            <li>correct inaccurate data</li>
            <li>delete your data, unless we must keep it by law</li>
            <li>restrict or object to processing, including direct marketing</li>
            <li>receive your data in a portable format</li>
            <li>withdraw consent at any time, without affecting earlier processing</li>
        </ul>
        <p>
            Email <a href="mailto:hello@funkydiandi.com">hello@funkydiandi.com</a> or call or WhatsApp <a href="tel:+971551094841">+971 55 109 4841</a>. We will reply within [30 days] and may ask you to confirm your identity. If your data is held for a brokerage, we will pass your request to them. If you are not satisfied, you can complain to the UAE Data Office.
        </p>
        <p>
            You can also submit an automated deletion request or track its progress on our dedicated <a href="/data-deletion">User Data Deletion Instructions</a> page.
        </p>

        <!-- Section 9 -->
        <h2 class="section-title">9. Cookies</h2>
        <p>
            <a href="https://andios.ae" target="_blank">andios.ae</a> does not currently use analytics, advertising or tracking cookies. It may set strictly necessary cookies the site needs to work, such as keeping you signed in. If we add analytics or other cookies, we will update this policy and ask for your consent first.
        </p>

        <!-- Section 10 -->
        <h2 class="section-title">10. Children</h2>
        <p>
            AndiOS is a business service. It is not meant for anyone under 18, and we do not knowingly collect their data.
        </p>

        <!-- Section 11 -->
        <h2 class="section-title">11. Changes to this policy</h2>
        <p>
            We will post updates on this page and change the date at the top. For significant changes, we will email customers before they take effect.
        </p>

        <!-- Section 12 -->
        <h2 class="section-title">12. Contact us</h2>
        <p>
            Funky Di Andi for Online Selling (Sole Establishment)<br>
            Dubai licence no. 1582068<br>
            Villa 103, New Dubai, P.O. Box 12345, Dubai, UAE<br>
            Email: <a href="mailto:hello@funkydiandi.com">hello@funkydiandi.com</a><br>
            Phone / WhatsApp: <a href="tel:+971551094841">+971 55 109 4841</a>
        </p>

        <div class="doc-footer" style="margin-top: 40px; padding-top: 24px; border-top: 1px solid #e5e7eb; font-size: 13px; color: #6b7280; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
            <span>&copy; 2026 Funky Di Andi for Online Selling. All rights reserved.</span>
            <span>
                <a href="/privacy">Privacy Policy</a> &bull;
                <a href="/terms">Terms of Service</a> &bull;
                <a href="/data-deletion">Data Deletion</a>
            </span>
        </div>
    </main>

</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse)
@router.get("/privacy-policy", response_class=HTMLResponse)
async def get_privacy_policy_page(request: Request):
    """
    Renders the public Privacy Policy webpage matching the official PDF document layout.
    Complies with Meta App Review & Developer Dashboard verification requirements.
    """
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse(content=api_success(data=PRIVACY_POLICY_DATA))

    return HTMLResponse(content=_render_privacy_html(), status_code=200)


@router.get("/api/privacy", response_class=JSONResponse)
@router.get("/api/privacy-policy", response_class=JSONResponse)
async def get_privacy_policy_json():
    """
    Returns structured Privacy Policy data in JSON format for API consumers and frontend apps.
    """
    return JSONResponse(
        status_code=200,
        content=api_success(
            data=PRIVACY_POLICY_DATA,
            message="Privacy Policy retrieved successfully"
        )
    )
