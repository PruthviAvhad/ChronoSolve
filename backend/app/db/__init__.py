"""Persistence: relational schema, database sessions and the repository.

PostgreSQL in deployment, SQLite for tests and zero-setup local runs -- one
schema on both. Only `repository` issues queries; the solver never does.
"""
