"""ZATCA Phase-2 e-invoicing (Fatoora) building blocks — UBL 2.1 XML, hash chain, XAdES signature, QR TLV, CSR/onboarding, API client.

STATUS: built from ZATCA's public specification and tested locally (structure, hashing, signature round-trip, chain integrity).
It has NOT been validated by ZATCA's SDK/validator or against ZATCA's servers from this repository. The whole module is behind
EINVOICE_ENABLED (off by default) and the client refuses to talk to the production endpoint unless two independent switches are on.
"""
