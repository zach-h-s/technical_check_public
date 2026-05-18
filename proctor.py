import os
from pathlib import Path

import duckdb
import pandas as pd
import yaml


class Proctor:
    """A simple class for managing the exam environment, including database connection
    and answer grading.
    """

    def __init__(
        self,
        user_email: str,
        db_path: str = "",
        answer_key_path: str = "",
        scores_output_path: str = "scores",
    ):
        """Initialize the Proctor with user email, database path, and answer key path.

        Parameters
        ----------
        user_email : str
            The email of the user taking the exam, used for identification.
        db_path : str
            The path to the DuckDB database file. If empty, uses the packaged database.
        answer_key_path : str
            The path to the YAML file containing the answer key. If empty, looks for it in the repo root.
        scores_output_path : str
            The path to the directory where scores will be saved.
        """

        repo_root = Path(__file__).resolve().parent.parent
        packaged_db_path = Path(__file__).resolve().parent / "healthcare_data.db"
        self.user_email = user_email

        # Use caller-provided DB path when valid; otherwise fall back to packaged DB.
        db_path_obj = Path(db_path).expanduser() if db_path else packaged_db_path
        if db_path and not db_path_obj.exists():
            db_path_obj = packaged_db_path
        if not db_path_obj.exists():
            raise FileNotFoundError(
                f"Database not found at {db_path_obj}. Expected packaged DB at {packaged_db_path}."
            )
        
        self.conn = duckdb.connect(str(db_path_obj), read_only=True)
        self.answers = {}
        self.scores = {}
        scores_output_root = os.environ.get("RA_EXAM_SCORES_OUTPUT", scores_output_path)
        self.scores_output_path = str(Path(scores_output_root).expanduser())

        # Missing answer key should disable grading instead of raising.
        answer_key_obj = (
            Path(answer_key_path).expanduser()
            if answer_key_path
            else repo_root / "data" / "questions.yaml"
        )
        self.grade = answer_key_obj.exists()
        if self.grade:
            with open(answer_key_obj, "r", encoding="utf-8") as file:
                self.answer_key = yaml.safe_load(file)


    def sql(self, query: str) -> pd.DataFrame:
        """Execute a SQL query against the connected database and
        return the result as a DataFrame.

        Parameters
        ----------
        query : str
            The SQL query to execute.

        Returns
        -------
        pd.DataFrame
            The result of the SQL query as a DataFrame.
        """
        try:
            return self.conn.execute(query).fetchdf()
        except Exception as e:
            print(f"SQL execution failed: {e}")
            return pd.DataFrame()

    def get_extra_credit(self, message: str):
        """If the user sees this method they have the opportunity to call it to
        add extra credit points to their score.
        """

        print(message)

        self.scores["extra_credit"] = {
            "points": 10,
            "points_possible": 0,
            "log": message,
        }

    def submit_answer(self, answer_df: pd.DataFrame, question_id: str):
        """Store the user's answer. If executed while an answer key is available, it
        will also grade the answer immediately.

        Parameters
        ----------
        answer_df : pd.DataFrame
            User's answer as a DataFrame.
        question_id : str
            The ID of the question being answered.
        """

        # Store answer
        self.answers[question_id] = answer_df

        # Grade on the spot if possible
        if self.grade:
            self.grade_answer(question_id)

    def get_answer(self, question_id: str) -> tuple[str, pd.DataFrame]:

        expected_answer = self.answer_key["questions"][question_id]["answer"]
        expected_df = self.sql(expected_answer)

        return expected_answer, expected_df

    def grade_answer(self, question_id: str):
        """Compare an answer to the expected answer and result.

        Parameters
        ----------
        question_id : str
            The ID of the question being graded.
        """

        try:
            expected_answer, expected_df = self.get_answer(question_id)
            points_possible = self.answer_key["questions"][question_id]["points"]
            pd.testing.assert_frame_equal(
                self.answers[question_id][expected_df.columns], expected_df
            )
            self.scores[question_id] = {
                "points": points_possible,
                "points_possible": points_possible,
                "log": "",
            }
            print(f"Question {question_id}: Correct")
        except Exception as e:
            points_possible = 0
            if self.grade and "questions" in self.answer_key and question_id in self.answer_key["questions"]:
                points_possible = self.answer_key["questions"][question_id].get("points", 0)
            self.scores[question_id] = {
                "points": 0,
                "points_possible": points_possible,
                "log": str(e),
            }
            print(f"Question {question_id}: Incorrect - {str(e)}")
            if "expected_answer" in locals():
                print(f"Expected SQL: {expected_answer}")

    def submit_score(self, question_id: str, points: int):
        """Override the score for a question.

        Parameters
        ----------
        question_id : str
            The ID of the question to override.
        points : int
            The score to assign. Replaces any existing score for the question.
        """

        self.scores[question_id]["points"] = points

    def show_and_save_grades(self) -> pd.DataFrame | None:
        """Show the grading results for all questions."""

        if not self.grade:
            print("No answer key available. Not grading.")
            return

        # Get data
        scores_df = pd.DataFrame(self.scores).T
        total_score = scores_df["points"].sum()
        print(f"Total Score: {total_score} out of {scores_df['points_possible'].sum()}")

        os.makedirs(self.scores_output_path, exist_ok=True)
        scores_df.to_csv(f"{self.scores_output_path}/{self.user_email}_scores.csv", index=True)
        print(f"Grades saved to {self.scores_output_path}/{self.user_email}_scores.csv")

        return scores_df

    def __del__(self):
        """Clean up database connection."""
        try:
            if hasattr(self, 'conn') and self.conn:
                self.conn.close()
        except Exception:
            pass
