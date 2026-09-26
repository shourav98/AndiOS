"""
Privacy Policy Router for AndiOS
Provides both HTML web page (for Meta/public verification) and JSON API endpoints.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from utils.response import api_success

router = APIRouter(tags=["Legal & Compliance"])

PRIVACY_POLICY_DATA = {
    "title": "Privacy Policy",
    "effective_date": "September 18, 2026",
    "last_updated": "September 18, 2026",
    "company": {
        "name": "AndiOS Technologies",
        "email": "privacy@andios.com",
        "website": "https://andi-os.vercel.app",
        "address": "Dubai, United Arab Emirates",
    },
    "introduction": (
        "AndiOS (\"AndiOS\", \"we\", \"our\", or \"us\") is committed to protecting the privacy and security "
        "of information processed through our website, application, and services (collectively, the \"Services\"). "
        "This Privacy Policy explains what information we collect, how we use it, how we protect it, when we may "
        "share it, and the choices available to you when using AndiOS. By accessing or using the Services, you "
        "acknowledge that you have read and understood this Privacy Policy."
    ),
    "sections": [
        {
            "id": 1,
            "title": "1. About AndiOS",
            "content": (
                "AndiOS is a business-to-business real-estate CRM and automation platform designed to help real-estate "
                "agencies manage their teams, agents, brands, leads, contacts, properties, communications, subscriptions, "
                "and related business operations.\n\n"
                "Organizations using AndiOS may enter or upload information relating to their agents, employees, customers, "
                "leads, prospects, property owners, buyers, sellers, and other business contacts.\n\n"
                "For information that an organization enters into AndiOS on behalf of its customers, leads, or other individuals, "
                "the organization may determine the purposes and means of processing, while AndiOS processes that information "
                "to provide the Services."
            )
        },
        {
            "id": 2,
            "title": "2. Information We Collect",
            "subsections": [
                {
                    "title": "2.1 Account and Profile Information",
                    "items": [
                        "Full name",
                        "Email address",
                        "Phone number",
                        "Password or authentication information",
                        "Profile information",
                        "Company or agency name",
                        "Business information",
                        "Role or position within an organization",
                        "Account preferences",
                        "Login and authentication information"
                    ]
                },
                {
                    "title": "2.2 Agency and Team Information",
                    "items": [
                        "Agency information",
                        "Brand information",
                        "Office or business details",
                        "Agent names and contact details",
                        "Agent roles and permissions",
                        "Team membership",
                        "Subscription and plan information",
                        "Usage and account activity"
                    ]
                },
                {
                    "title": "2.3 CRM and Real-Estate Data",
                    "content": (
                        "Depending on the features used by an organization, AndiOS may process: lead and contact information, "
                        "names, email addresses, phone numbers, property information, property preferences, buyer and seller information, "
                        "notes and follow-up information, communication history, appointment information, agent activity, lead qualification "
                        "information, CRM records, and related business information. Organizations are responsible for ensuring that they "
                        "have an appropriate legal basis and authorization to collect and process information they upload to AndiOS."
                    )
                },
                {
                    "title": "2.4 Communication Data",
                    "content": (
                        "If communication features are enabled, AndiOS may process information associated with: WhatsApp messages, "
                        "SMS messages, voice calls, call metadata, call recordings (where applicable and legally permitted), transcriptions, "
                        "appointment and scheduling information, automated messages, AI-generated responses, and communication history. "
                        "The availability and exact processing of this information depends on the communication services and integrations "
                        "configured by the organization."
                    )
                },
                {
                    "title": "2.5 Payment and Subscription Information",
                    "content": (
                        "When an organization purchases a subscription, plan, add-on, or other paid service, we may process information "
                        "relating to: subscription plan, billing status, transaction information, invoice information, payment status, "
                        "subscription dates, and usage and billing-related records. Payment card information is processed directly by our "
                        "payment service providers (e.g., Stripe). We do not store complete payment card numbers on our own systems."
                    )
                },
                {
                    "title": "2.6 Technical and Usage Information",
                    "content": (
                        "We may automatically collect technical information when you access the Services, including: IP address, browser type, "
                        "device type, operating system, login timestamps, pages or features accessed, session information, error and diagnostic "
                        "information, security and audit logs, and general usage information. This information helps us maintain security, "
                        "troubleshoot technical problems, and improve the reliability of the Services."
                    )
                }
            ]
        },
        {
            "id": 3,
            "title": "3. How We Use Information",
            "items": [
                "Create and manage user accounts and authenticate users",
                "Provide CRM and real-estate management functionality",
                "Manage agencies, teams, agents, and brands",
                "Manage leads, contacts, and property information",
                "Provide communication and automation features",
                "Process authorized WhatsApp, messaging, and calling integrations",
                "Provide AI-powered features and automation",
                "Qualify leads and assist with follow-ups",
                "Support appointment scheduling and related workflows",
                "Process subscriptions and payments",
                "Monitor service usage and subscription limits",
                "Provide customer support",
                "Maintain and improve the Services",
                "Detect and prevent fraud, abuse, and unauthorized access",
                "Maintain system security and troubleshoot technical problems",
                "Comply with applicable legal obligations and enforce our agreements and policies",
                "Communicate important service, security, or account-related information"
            ]
        },
        {
            "id": 4,
            "title": "4. AI Features and Processing",
            "content": (
                "AndiOS may provide AI-powered features, including automated responses, lead qualification, communication "
                "assistance, voice automation, appointment-related workflows, and other AI functionality.\n\n"
                "When you use an AI feature, relevant information may be processed by AndiOS and, where required to provide "
                "the feature, by trusted third-party AI or technology providers. The information processed may include prompts, "
                "CRM information, communication content, contact information, property information, call transcripts, and "
                "other information necessary to provide the requested functionality.\n\n"
                "We use AI processing only to provide, maintain, secure, and improve the Services. We do NOT sell customer CRM "
                "data. Where third-party AI providers are used, their processing is also subject to applicable terms, privacy policies, "
                "and data-processing agreements."
            )
        },
        {
            "id": 5,
            "title": "5. WhatsApp, Voice, and Communication Integrations",
            "content": (
                "AndiOS integrates with third-party communication platforms including Meta WhatsApp Cloud API, voice providers, "
                "and messaging providers. If an organization connects a communication account or phone number to AndiOS, the platform "
                "processes information required to:\n"
                "• Receive and send authorized messages\n"
                "• Automate responses and qualify leads\n"
                "• Create or update CRM contacts\n"
                "• Maintain communication history and audit trails\n"
                "• Schedule reminders, appointments, and follow-ups\n"
                "• Monitor communication usage and quota limits\n\n"
                "The organization connecting such services is responsible for ensuring that its use of these communication features "
                "complies with applicable laws, platform policies (including Meta WhatsApp Business Policy), and required consent requirements."
            )
        },
        {
            "id": 6,
            "title": "6. Third-Party Services and Integrations",
            "content": (
                "AndiOS may use or integrate with third-party services to provide certain functionality, including providers for: "
                "cloud hosting and infrastructure, database and storage, authentication, email delivery, payment processing, AI services, "
                "voice and telephony, WhatsApp and messaging, analytics, monitoring, security, customer support, and property portals "
                "(e.g., Property Finder, Bayut, Dubizzle). Third-party providers process information on our behalf under strict "
                "security standards."
            )
        },
        {
            "id": 7,
            "title": "7. How We Share Information",
            "content": (
                "We do NOT sell personal information or customer CRM data. We disclose information only to: trusted service providers "
                "operating the Services, authorized integrations explicitly connected by users/organizations, legal and regulatory authorities "
                "when required by law, security personnel to protect against fraud or abuse, and relevant parties in the event of a merger, "
                "acquisition, or asset transfer."
            )
        },
        {
            "id": 8,
            "title": "8. Data Security",
            "content": (
                "We use reasonable administrative, technical, and organizational safeguards designed to protect information against "
                "unauthorized access, alteration, disclosure, loss, or destruction. Security measures include: encrypted connections "
                "(HTTPS/TLS), encryption at rest for sensitive API tokens (AES-256-GCM), access controls, multi-factor authentication, "
                "role-based permissions, database row-level security (RLS), security monitoring, and regular backups."
            )
        },
        {
            "id": 9,
            "title": "9. Data Retention",
            "content": (
                "We retain information for as long as reasonably necessary to provide the Services, maintain active accounts, "
                "fulfill contractual obligations, maintain security and audit records, resolve disputes, prevent fraud, and comply "
                "with legal requirements. Organizations may request account or data deletion at any time."
            )
        },
        {
            "id": 10,
            "title": "10. Data Ownership",
            "content": (
                "Organizations retain full ownership and control of the business and CRM information they submit to AndiOS. "
                "AndiOS does not claim ownership of customer CRM records, leads, contacts, or property listings. We process "
                "such information solely to provide the Services."
            )
        },
        {
            "id": 11,
            "title": "11. International Data Processing",
            "content": (
                "AndiOS and its service providers operate globally. Information may be processed and stored in secure data centers "
                "located in jurisdictions other than your country of residence, utilizing industry-standard transfer safeguards."
            )
        },
        {
            "id": 12,
            "title": "12. Cookies and Similar Technologies",
            "content": (
                "We use necessary cookies and session tokens to authenticate users, maintain secure sessions, remember preferences, "
                "and ensure service reliability. You may adjust cookie preferences in your browser settings."
            )
        },
        {
            "id": 13,
            "title": "13. Your Privacy Rights",
            "content": (
                "Depending on your location and applicable law (such as GDPR, CCPA, or UAE Data Protection Law), you may have rights "
                "to access, correct, delete, restrict, or export your personal information, or withdraw consent. Contact us at "
                "privacy@andios.com to exercise these rights."
            )
        },
        {
            "id": 14,
            "title": "14. Account and Data Deletion Requests",
            "content": (
                "Users may request deletion of their account or personal data by emailing privacy@andios.com. We verify identity "
                "prior to fulfilling requests and complete deletions within standard regulatory timelines."
            )
        },
        {
            "id": 15,
            "title": "15. Children's Privacy",
            "content": (
                "AndiOS is an enterprise B2B platform intended solely for professional business use. We do not knowingly collect "
                "personal data from individuals under 18 years of age."
            )
        },
        {
            "id": 16,
            "title": "16. Third-Party Links",
            "content": (
                "The Services may contain links to third-party sites or portals. We are not responsible for the privacy practices "
                "or content of external third-party sites."
            )
        },
        {
            "id": 17,
            "title": "17. Changes to This Privacy Policy",
            "content": (
                "We may update this Privacy Policy periodically. Any revisions will be reflected by the 'Last Updated' date at the top "
                "of this page. Continued use of the Services signifies acceptance of updated terms."
            )
        },
        {
            "id": 18,
            "title": "18. Contact Us",
            "content": (
                "If you have questions about this Privacy Policy or wish to exercise your data privacy rights, please contact us:\n"
                "• Company: AndiOS Technologies\n"
                "• Email: privacy@andios.com\n"
                "• Website: https://andi-os.vercel.app\n"
                "• Address: Dubai, United Arab Emirates"
            )
        },
        {
            "id": 19,
            "title": "19. Effective Date",
            "content": "This Privacy Policy is effective as of September 18, 2026."
        }
    ]
}


def _render_privacy_html() -> str:
    """Generate high-quality, modern, accessible HTML version for web browsers and Meta crawlers."""
    sections_html = ""
    for sec in PRIVACY_POLICY_DATA["sections"]:
        sec_title = sec["title"]
        sec_body = ""
        if "content" in sec:
            paras = sec["content"].split("\n\n")
            for p in paras:
                if p.startswith("• "):
                    items = [f"<li>{line.replace('• ', '')}</li>" for line in p.split("\n") if line.strip()]
                    sec_body += f"<ul class='list'>{ ''.join(items) }</ul>"
                else:
                    sec_body += f"<p>{p.replace(chr(10), '<br>')}</p>"
        elif "items" in sec:
            items = "".join(f"<li>{item}</li>" for item in sec["items"])
            sec_body += f"<ul class='list'>{items}</ul>"
        elif "subsections" in sec:
            for sub in sec["subsections"]:
                sec_body += f"<h3 class='subsection-title'>{sub['title']}</h3>"
                if "items" in sub:
                    sub_items = "".join(f"<li>{item}</li>" for item in sub["items"])
                    sec_body += f"<ul class='list'>{sub_items}</ul>"
                elif "content" in sub:
                    sec_body += f"<p>{sub['content']}</p>"

        sections_html += f"""
        <section class="policy-section">
            <h2>{sec_title}</h2>
            {sec_body}
        </section>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy | AndiOS CRM</title>
    <meta name="description" content="AndiOS Real Estate CRM and Automation Platform Privacy Policy. Learn how we handle, protect, and respect your data.">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: #111827;
            --border-color: #1f2937;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --text-muted: #6b7280;
            --accent: #3b82f6;
            --accent-glow: rgba(59, 130, 246, 0.15);
        }}
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            line-height: 1.7;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 860px;
            margin: 0 auto;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 48px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        }}
        header {{
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 28px;
            margin-bottom: 36px;
        }}
        .badge {{
            display: inline-block;
            background: var(--accent-glow);
            color: var(--accent);
            font-size: 13px;
            font-weight: 600;
            padding: 4px 12px;
            border-radius: 9999px;
            margin-bottom: 14px;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }}
        h1 {{
            font-size: 32px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin-bottom: 8px;
            color: #ffffff;
        }}
        .meta-info {{
            color: var(--text-muted);
            font-size: 14px;
        }}
        .intro {{
            font-size: 16px;
            color: var(--text-secondary);
            margin-bottom: 32px;
            padding: 18px 20px;
            background: rgba(255, 255, 255, 0.02);
            border-left: 3px solid var(--accent);
            border-radius: 0 8px 8px 0;
        }}
        .policy-section {{
            margin-bottom: 32px;
        }}
        h2 {{
            font-size: 20px;
            font-weight: 600;
            color: #ffffff;
            margin-bottom: 14px;
            padding-top: 8px;
        }}
        .subsection-title {{
            font-size: 16px;
            font-weight: 600;
            color: #d1d5db;
            margin: 18px 0 8px 0;
        }}
        p {{
            color: var(--text-secondary);
            font-size: 15px;
            margin-bottom: 12px;
        }}
        .list {{
            list-style: none;
            padding-left: 0;
            margin: 12px 0 16px 0;
        }}
        .list li {{
            position: relative;
            padding-left: 24px;
            margin-bottom: 8px;
            color: var(--text-secondary);
            font-size: 15px;
        }}
        .list li::before {{
            content: "•";
            position: absolute;
            left: 8px;
            color: var(--accent);
            font-weight: bold;
        }}
        footer {{
            margin-top: 48px;
            padding-top: 24px;
            border-top: 1px solid var(--border-color);
            text-align: center;
            color: var(--text-muted);
            font-size: 13px;
        }}
        footer a {{
            color: var(--accent);
            text-decoration: none;
        }}
        footer a:hover {{
            text-decoration: underline;
        }}
        @media (max-width: 640px) {{
            .container {{
                padding: 24px;
            }}
            h1 {{
                font-size: 26px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <span class="badge">AndiOS Enterprise Compliance</span>
            <h1>Privacy Policy</h1>
            <div class="meta-info">
                <span>Effective Date: {PRIVACY_POLICY_DATA["effective_date"]}</span> &bull; 
                <span>Last Updated: {PRIVACY_POLICY_DATA["last_updated"]}</span>
            </div>
        </header>

        <div class="intro">
            {PRIVACY_POLICY_DATA["introduction"]}
        </div>

        <main>
            {sections_html}
        </main>

        <footer>
            <p>&copy; 2026 AndiOS Technologies. All rights reserved. &bull; <a href="{PRIVACY_POLICY_DATA["company"]["website"]}" target="_blank">AndiOS Platform</a></p>
        </footer>
    </div>
</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse)
@router.get("/privacy-policy", response_class=HTMLResponse)
async def get_privacy_policy_page(request: Request):
    """
    Renders the public Privacy Policy webpage.
    Complies with Meta App Review & Developer Dashboard verification requirements.
    """
    # If a client specifically asks for JSON via Accept header, deliver JSON
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
