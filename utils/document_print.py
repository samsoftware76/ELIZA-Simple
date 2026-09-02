"""
Builds the render context for the shared print/PDF view (templates/print/document.html)
from a Quote or Invoice, used by both the staff-facing routes (api/billing.py) and the
public client portal routes (api/portal.py) so both sides see the exact same document.
"""
from models import User


def build_print_context(doc, doc_type):
    """
    Args:
        doc: a Quote or Invoice instance
        doc_type (str): 'Quote' or 'Invoice'
    """
    # business_name/business_logo_url are tenant-level fields only ever
    # written onto the account owner's own row (see /profile) - a document
    # created by a team member must resolve branding through get_owner_id()
    # to the owner's row, not the creator's own (normally NULL) row.
    creator = User.query.get(doc.created_by.get_owner_id()) if doc.created_by else None
    client = doc.client

    if doc_type == 'Quote':
        doc_number = doc.quote_number
        due_date = doc.valid_until
    else:
        doc_number = doc.invoice_number
        due_date = doc.due_date

    return {
        'doc_type': doc_type,
        'doc_number': doc_number,
        'brand_name': (creator.business_name if creator else None) or 'ELIZA',
        'brand_logo_url': (creator.business_logo_url if creator else None) or None,
        'from_name': creator.get_full_name() if creator else '',
        'from_email': creator.email if creator else '',
        'from_phone': getattr(creator, 'phone', '') if creator else '',
        'client_name': client.name,
        'client_contact': client.contact_person,
        'client_email': client.email,
        'client_phone': client.phone,
        'issue_date': doc.created_at.strftime('%B %d, %Y') if doc.created_at else '',
        'due_date': due_date.strftime('%B %d, %Y') if due_date else None,
        'status': doc.status,
        'status_label': doc.status.replace('_', ' ').title(),
        'title': doc.title,
        'items': doc.items,
        'subtotal': doc.subtotal,
        'tax_rate': doc.tax_rate,
        'tax_amount': doc.tax_amount,
        'total': doc.total,
        'currency': doc.currency,
        'notes': doc.notes,
    }


def build_contract_print_context(contract, body_text):
    """
    Render context for templates/print/contract.html, shared by the staff
    print route (api/contracts.py) and the public portal print route
    (api/portal.py) so both sides print the exact same document.

    Args:
        contract: a Contract instance
        body_text (str): the contract text to print. The caller chooses:
            the portal passes body_snapshot ONLY (the frozen legal text a
            client sees - never the live working copy), while the staff
            route passes body_snapshot-or-body so a draft can be previewed.
    """
    # Same tenant-branding resolution as build_print_context above: branding
    # lives on the account owner's row, so resolve through get_owner_id().
    creator = User.query.get(contract.created_by.get_owner_id()) if contract.created_by else None
    client = contract.client

    return {
        'contract': contract,
        'body_text': body_text or '',
        'doc_number': contract.contract_number,
        'brand_name': (creator.business_name if creator else None) or 'ELIZA',
        'brand_logo_url': (creator.business_logo_url if creator else None) or None,
        'from_name': creator.get_full_name() if creator else '',
        'from_email': creator.email if creator else '',
        'from_phone': getattr(creator, 'phone', '') if creator else '',
        'client_name': client.name,
        'client_contact': client.contact_person,
        'client_email': client.email,
        'client_phone': client.phone,
        'issue_date': contract.created_at.strftime('%B %d, %Y') if contract.created_at else '',
        'status': contract.status,
        'status_label': contract.status.replace('_', ' ').title(),
    }
