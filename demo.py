from ai_company.company import Company

company = Company(max_workers=3, verbose=True)

issue = "Add a /login endpoint with email+password and a login form page."
graph = company.solve_issue(issue)

print("\n=== FINAL TASK GRAPH ===")
print(graph.summary())

print("\n=== SAMPLE OUTPUT (tech lead) ===")
print(graph.tasks["plan"].result)
