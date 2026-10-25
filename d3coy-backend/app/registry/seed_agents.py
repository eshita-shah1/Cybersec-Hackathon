"""Example agent profiles for the prototype. These are demo policies, not universal security rules."""

from __future__ import annotations

from app.schemas.agents import AgentCreate

RESEARCH_AGENT_ID = "research-agent-01"
FINANCE_AGENT_ID = "finance-agent-01"
EMAIL_AGENT_ID = "email-agent-01"
PROCUREMENT_AGENT_ID = "procurement-agent-01"

SEED_AGENTS: list[AgentCreate] = [
    AgentCreate(
        agent_id=RESEARCH_AGENT_ID,
        agent_name="Research Agent",
        role="researcher",
        assigned_task="Research renewable energy storage and summarize public information",
        task_description=(
            "Research renewable energy storage technologies such as batteries and hydrogen, "
            "search and read publicly available sources, and write a summary of the findings."
        ),
        allowed_tools=["search_public_web", "read_public_document", "generate_summary"],
        forbidden_actions=["delete", "export", "send", "access_payroll", "delete_customer_records",
                           "export_confidential_data"],
        allowed_resources=["public:*", "research:notes/*"],
        restricted_resources=["finance:*", "customers:*", "hr:*"],
        resource_permissions=[
            {"resource": "public:*", "operations": ["search", "read"], "max_sensitivity": "public"},
            {"resource": "research:notes/*", "operations": ["read", "compute", "write"], "max_sensitivity": "internal"},
        ],
    ),
    AgentCreate(
        agent_id=FINANCE_AGENT_ID,
        agent_name="Finance Agent",
        role="financial_analyst",
        assigned_task="Analyze monthly expenses and calculate spending by category",
        task_description=(
            "Analyze monthly company expenses, calculate the total spending for each expense category, "
            "and produce a financial summary of spending."
        ),
        allowed_tools=["read_expenses", "calculate_totals", "generate_financial_summary", "read_vendor_payments"],
        forbidden_actions=["delete", "export", "send", "delete_customer_records", "export_confidential_data"],
        allowed_resources=["finance:expenses/*", "finance:reports/*", "finance:vendor_payments/*"],
        restricted_resources=["finance:payroll*", "customers:*", "hr:*"],
        resource_permissions=[
            {"resource": "finance:expenses/*", "operations": ["read", "compute"], "max_sensitivity": "internal"},
            {"resource": "finance:reports/*", "operations": ["read", "write"], "max_sensitivity": "internal"},
            {"resource": "finance:vendor_payments/*", "operations": ["read"], "max_sensitivity": "confidential"},
        ],
    ),
    AgentCreate(
        agent_id=EMAIL_AGENT_ID,
        agent_name="Email Agent",
        role="email_assistant",
        assigned_task="Review approved information and prepare email drafts",
        task_description=(
            "Read approved email content, summarize the messages, and prepare email drafts for the team. "
            "Emails are only sent when explicitly authorized."
        ),
        allowed_tools=["read_approved_emails", "summarize_messages", "create_email_draft", "send_email"],
        forbidden_actions=["delete", "export", "delete_customer_records", "export_confidential_data"],
        allowed_resources=["email:approved/*", "email:drafts/*", "email:outbox"],
        restricted_resources=["email:confidential/*", "customers:*", "finance:*", "hr:*"],
        resource_permissions=[
            {"resource": "email:approved/*", "operations": ["read", "compute"], "max_sensitivity": "internal"},
            {"resource": "email:drafts/*", "operations": ["read", "write"], "max_sensitivity": "internal"},
            {"resource": "email:outbox", "operations": ["send"], "max_sensitivity": "internal"},
        ],
        # Sending is in scope only with per-call human authorization ("unless explicitly authorized").
        review_required_tools=["send_email"],
        external_transfer_allowed=False,
        allowed_destinations=["*@d3coy.local"],
    ),
    AgentCreate(
        agent_id=PROCUREMENT_AGENT_ID,
        agent_name="Procurement Agent",
        role="procurement_assistant",
        assigned_task="Compare supplier prices and prepare a purchase summary",
        task_description=(
            "Read the supplier emails and the supplier catalog, compare prices across suppliers, "
            "and prepare a purchase summary. Send the summary to the procurement team only when "
            "explicitly authorized."
        ),
        # Uses the REAL (sandboxed) tools executed by the executor service.
        allowed_tools=["search_supplier_catalog", "read_supplier_emails", "send_purchase_summary"],
        forbidden_actions=["delete", "export", "delete_customer", "export_customer_data",
                           "delete_customer_records", "export_confidential_data"],
        allowed_resources=["supplier:catalog*", "supplier:emails*", "procurement:outbox"],
        restricted_resources=["customers:*", "finance:*", "hr:*", "email:*"],
        resource_permissions=[
            {"resource": "supplier:catalog*", "operations": ["search", "read"], "max_sensitivity": "internal"},
            {"resource": "supplier:emails*", "operations": ["read"], "max_sensitivity": "internal"},
            {"resource": "procurement:outbox", "operations": ["send"], "max_sensitivity": "internal"},
        ],
        review_required_tools=["send_purchase_summary"],
        external_transfer_allowed=False,
        allowed_destinations=["*@d3coy.local"],
    ),
]
