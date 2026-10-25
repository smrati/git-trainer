I want to create a full stack web application in python where backend will be fastapi, frontend will be vanilla javascript+html+css.
If you need a database use sqlite

The purpose of this application is that this application will be used to learn and practice git by software professionals irrespective of their experience level.

This application will teach you git and help you practice it and evaluate you as well.

Grading is by reference solution, not by a lookup table: every exercise stores a reference script of git commands and a list of checks, and a submission is run in a copy of the exercise's seed and compared against what the reference produces. Any correct implementation passes.

The learner's answer is a script of git commands, so grading compares *repository state* rather than a single output: the reference script and the learner's script each run in an identical throwaway repository, and the resulting repositories are compared. Commit hashes are never graded -- the sandbox pins the identity and the clock, and the comparison scrubs hashes, paths and dates, so what is graded is structure.
