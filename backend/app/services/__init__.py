"""Application services: the layer between HTTP and the solver.

Services turn stored, human-managed data -- institutional rules, temporary
overrides, requests, versions -- into the plain `Instance` the CP-SAT engine
understands, and turn solver results back into records. The solver never sees
SQL, and nothing here builds constraint models.
"""
