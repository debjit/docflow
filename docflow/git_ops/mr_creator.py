"""
Platform-agnostic Merge Request / Pull Request creator.
"""

import os
from typing import Optional, Dict, Any
import httpx


class MRCreator:
    """Creates Pull Requests or Merge Requests across GitHub, GitLab, and Bitbucket."""

    def __init__(self, platform_type: str = "github"):
        self.platform_type = platform_type.lower()

    def create_mr(
        self,
        title: str,
        body: str,
        source_branch: str,
        target_branch: str = "main",
        repo_owner_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates an MR/PR on the configured platform."""
        if self.platform_type == "github":
            return self._create_github_pr(title, body, source_branch, target_branch, repo_owner_name)
        elif self.platform_type == "gitlab":
            return self._create_gitlab_mr(title, body, source_branch, target_branch, repo_owner_name)
        else:
            return {
                "success": True,
                "platform": "generic",
                "message": f"Branch '{source_branch}' ready. Create PR manually targeting '{target_branch}'."
            }

    def _create_github_pr(
        self, title: str, body: str, source_branch: str, target_branch: str, repo_owner_name: Optional[str]
    ) -> Dict[str, Any]:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            return {
                "success": False,
                "error": "GITHUB_TOKEN environment variable not set."
            }

        repo_slug = repo_owner_name or os.getenv("GITHUB_REPOSITORY")
        if not repo_slug:
            return {
                "success": False,
                "error": "Repository owner/name (e.g. 'owner/repo') must be provided."
            }

        url = f"https://api.github.com/repos/{repo_slug}/pulls"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {
            "title": title,
            "body": body,
            "head": source_branch,
            "base": target_branch,
        }

        try:
            res = httpx.post(url, json=payload, headers=headers, timeout=30.0)
            if res.status_code in (200, 201):
                data = res.json()
                return {
                    "success": True,
                    "platform": "github",
                    "mr_url": data.get("html_url"),
                    "mr_id": data.get("number"),
                }
            else:
                return {
                    "success": False,
                    "platform": "github",
                    "error": f"HTTP {res.status_code}: {res.text}"
                }
        except Exception as e:
            return {"success": False, "platform": "github", "error": str(e)}

    def _create_gitlab_mr(
        self, title: str, body: str, source_branch: str, target_branch: str, project_id: Optional[str]
    ) -> Dict[str, Any]:
        token = os.getenv("GITLAB_PRIVATE_TOKEN") or os.getenv("GITLAB_TOKEN")
        if not token:
            return {
                "success": False,
                "error": "GITLAB_PRIVATE_TOKEN environment variable not set."
            }

        proj = project_id or os.getenv("CI_PROJECT_ID")
        if not proj:
            return {
                "success": False,
                "error": "GitLab project_id (e.g. '123456' or 'group/project') must be provided."
            }

        # Encode project id for URL
        encoded_proj = proj.replace("/", "%2F")
        gitlab_host = os.getenv("GITLAB_HOST", "https://gitlab.com")
        url = f"{gitlab_host}/api/v4/projects/{encoded_proj}/merge_requests"
        headers = {"PRIVATE-TOKEN": token}
        payload = {
            "title": title,
            "description": body,
            "source_branch": source_branch,
            "target_branch": target_branch,
        }

        try:
            res = httpx.post(url, json=payload, headers=headers, timeout=30.0)
            if res.status_code in (200, 201):
                data = res.json()
                return {
                    "success": True,
                    "platform": "gitlab",
                    "mr_url": data.get("web_url"),
                    "mr_id": data.get("iid"),
                }
            else:
                return {
                    "success": False,
                    "platform": "gitlab",
                    "error": f"HTTP {res.status_code}: {res.text}"
                }
        except Exception as e:
            return {"success": False, "platform": "gitlab", "error": str(e)}
