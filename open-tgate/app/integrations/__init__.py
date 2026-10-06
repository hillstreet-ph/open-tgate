"""Downstream integrations for Open-TGate.

Integrations are deliberately kept out of the Telegram sync path: the sync
worker only writes durable records, and these modules run on the API side so a
third-party outage can never stall account login or entity sync.
"""
