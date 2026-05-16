Your task is to design and implement a GitLab Merge Request Review Agent.

Main goal:
The agent should review GitLab Merge Requests and provide meaningful engineering feedback on the changes.

Important behavior:
The review must NOT be annoying, noisy, or overly strict.
Do not suggest changes just for the sake of suggesting something.
Do not comment on every small detail.
Focus on issues that are genuinely useful, important, or architecturally meaningful.

The agent should analyze the MR as a whole:
- Understand the purpose of the merge request.
- Read the changed files and diffs.
- Identify the main areas affected by the change.
- Consider the broader architecture and project patterns before giving feedback.
- Avoid isolated comments that ignore the context of the whole MR.

The review should prioritize:
1. Architectural correctness
   - Does the solution fit the existing architecture?
   - Is the responsibility placed in the correct layer/module?
   - Is the abstraction appropriate?
   - Is the change reusable where needed, but not over-engineered?
   - Does it follow existing project conventions?

2. Pythonic code quality
   - Clear and idiomatic Python.
   - Simple and readable control flow.
   - Early returns where they make the code easier to read.
   - Avoid unnecessary nesting.
   - Avoid duplicated logic.
   - Good variable, function, and class names.
   - Good use of typing where useful.
   - Avoid overly complex expressions.
   - Avoid premature abstraction.

3. Engineering quality
   - Clear separation of concerns.
   - Good error handling.
   - Good logging where appropriate.
   - Safe handling of edge cases.
   - Avoid hidden side effects.
   - Avoid unnecessary database queries or inefficient loops.
   - Consider performance only when it is relevant.
   - Consider security risks if the code touches auth, permissions, tokens, files, external services, or user input.

4. Tests
   - Check whether important behavior is covered by tests.
   - Suggest tests only when the change introduces meaningful logic or risk.
   - Do not request tests for trivial changes unless there is a clear reason.

5. Maintainability
   - Is the code easy to understand?
   - Is the logic placed where future developers would expect it?
   - Are names clear?
   - Is the implementation too clever or too complicated?

Review style:
- Be concise.
- Be practical.
- Be respectful.
- Avoid nitpicking.
- Avoid generic comments.
- Do not repeat the same point many times.
- Only leave a comment if it is actionable.
- If the MR is good, say that there are no major issues.
- Prefer fewer, higher-quality comments over many minor comments.

Comment severity:
Classify findings into one of these levels:
- blocking: must be fixed before merge
- important: should be fixed, but may not block merge depending on context
- suggestion: optional improvement
- praise: good decision worth mentioning

The agent should not block an MR because of style preferences only.
Blocking comments should be reserved for:
- bugs
- broken architecture
- security issues
- incorrect business logic
- serious maintainability problems
- missing critical tests
- changes that can break existing behavior

The agent should produce:
1. A short general summary of the MR.
2. A short risk assessment.
3. A list of review comments.
4. Optional positive feedback if something is well done.
5. A final recommendation:
   - approve
   - approve_with_suggestions
   - request_changes

Expected output format:

{
  "summary": "...",
  "risk_assessment": "...",
  "comments": [
    {
      "severity": "blocking | important | suggestion | praise",
      "file_path": "...",
      "line": 123,
      "message": "...",
      "reason": "...",
      "suggested_change": "..."
    }
  ],
  "recommendation": "approve | approve_with_suggestions | request_changes"
}

Implementation requirements:
- Create a clean service/agent structure.
- Do not hardcode GitLab API details inside the review logic.
- Reuse the existing GitLab HTTP client.
- Separate GitLab data fetching from MR analysis.
- Separate prompt building from API/client logic.
- Make the agent easy to extend later, for example for:
  - commenting directly on GitLab discussions
  - reviewing pipelines
  - summarizing issues
  - checking changed files against project rules

Suggested architecture:
- GitLabMRReviewAgent
  - orchestrates the review process
- GitLabMRService
  - loads MR metadata, changed files, diffs, discussions if needed
- MRReviewPromptBuilder
  - builds the LLM prompt for review
- MRReviewResult
  - typed response model for parsed review output
- MRReviewComment
  - typed model for each comment
- Recommendation enum
- Severity enum

The implementation should be production-oriented but not over-engineered.

Use Pydantic models for structured outputs if the project already uses Pydantic.
Use async code if the existing GitLab client is async.
Follow the existing project style and folder structure.

Do not implement actual GitLab posting/commenting unless there is already a clear method in the existing client. For now, it is enough to produce a structured review result that can later be posted to GitLab.

Before coding:
1. Inspect the project structure.
2. Find the existing GitLab HTTP client.
3. Understand existing service and agent patterns.
4. Reuse existing conventions.
5. Then implement the MR review agent.

After coding:
1. Add or update tests for the review agent.
2. Add a short usage example.
3. Make sure typing is correct.
4. Make sure the code is readable and not overcomplicated.