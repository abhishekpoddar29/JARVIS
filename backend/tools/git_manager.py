import os
import logging
from git import Repo, InvalidGitRepositoryError

logger = logging.getLogger(__name__)

# Point to the parent directory (Jarvis-0.2/) where .git is located
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

class GitManager:
    def __init__(self):
        self.repo = None
        try:
            self.repo = Repo(PROJECT_ROOT)
            logger.info("[GitManager] Git repository initialized successfully.")
        except InvalidGitRepositoryError:
            logger.warning(f"[GitManager] Not a valid Git repository at {PROJECT_ROOT}. Self-modification will be disabled.")
        except Exception as e:
            logger.error(f"[GitManager] Failed to initialize Git: {e}")

    def create_branch(self, branch_name: str) -> bool:
        if not self.repo: return False
        try:
            # Get the current active branch
            current_branch = self.repo.active_branch.name
            
            # If we are already on the feature branch, we don't need to create it again
            if current_branch == branch_name:
                return True
                
            # Create and checkout the new branch from the current HEAD
            self.repo.git.checkout('-b', branch_name)
            logger.info(f"[GitManager] Created and switched to branch: {branch_name} (from {current_branch})")
            return True
        except Exception as e:
            logger.error(f"[GitManager] Failed to create branch: {e}")
            return False

    def commit_file(self, filepath: str, message: str) -> bool:
        if not self.repo: return False
        try:
            self.repo.index.add([filepath])
            self.repo.index.commit(message)
            return True
        except Exception as e:
            logger.error(f"[GitManager] Failed to commit: {e}")
            return False

    def revert_and_delete_branch(self, branch_name: str) -> bool:
        if not self.repo: return False
        try:
            self.repo.git.checkout('main')
            self.repo.git.branch('-D', branch_name)
            return True
        except Exception as e:
            logger.error(f"[GitManager] Failed to revert: {e}")
            return False

git_manager = GitManager()