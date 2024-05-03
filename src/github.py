from datetime import datetime
from time import sleep

from src import requests

from python_graphql_client import GraphqlClient

class Github:
    def __init__(self, repo: str, token: str, base_url: str, owner: str):
        self.token = token
        self.repo = repo
        self.base_url = base_url
        self.owner = owner
        self.client = GraphqlClient(endpoint="https://api.github.com/graphql")

    def make_headers(self) -> dict:
        return {
            'authorization': f'Bearer {self.token}',
            'content-type': 'application/vnd.github.v3+json',
        }

    def get_paginated_branches_url(self, page: int = 0) -> str:
        return f'{self.base_url}/repos/{self.repo}/branches?protected=false&per_page=30&page={page}'

    def get_deletable_branches(
            self,
            last_commit_age_days: int,
            ignore_branches: list[str],
            allowed_prefixes: list[str],
            branch_limit: int,
    ) -> list[str]:
        if branch_limit < 1:
            return []

        # Default branch might not be protected
        default_branch = self.get_default_branch()

        url = self.get_paginated_branches_url()
        headers = self.make_headers()

        response = requests.get(url=url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f'Failed to make request to {url}. {response} {response.json()}')

        deletable_branches = []
        branch: dict
        branches: list = response.json()
        current_page = 1

        while len(branches) > 0:
            for branch in branches:
                branch_name = branch.get('name')

                commit_hash = branch.get('commit', {}).get('sha')
                commit_url = branch.get('commit', {}).get('url')

                print(f'Analyzing branch `{branch_name}`...')

                # Immediately discard protected branches, default branch, ignored branches and branches not in prefix
                if branch_name == default_branch:
                    print(f'Ignoring `{branch_name}` because it is the default branch')
                    continue

                # We're already retrieving non-protected branches from the API, but it pays being careful when dealing
                # with third party apis
                if branch.get('protected') is True:
                    print(f'Ignoring `{branch_name}` because it is protected')
                    continue
                
                found_ignored_prefix = False
                for prefix in ignore_branches:
                    if branch_name.startswith(prefix):
                        found_ignored_prefix = True
                        break
                if found_ignored_prefix is True:
                    print(f'Ignoring `{branch_name}` because it is on the list of ignored branch prefixes')
                    continue

                # If allowed_prefixes are provided, only consider branches that match one of the prefixes
                if len(allowed_prefixes) > 0:
                    found_prefix = False
                    for prefix in allowed_prefixes:
                        if branch_name.startswith(prefix):
                            found_prefix = True
                    if found_prefix is False:
                        print(f'Ignoring `{branch_name}` because it does not match any provided allowed_prefixes')
                        continue

                # Move on if commit is in an open pull request
                if self.has_open_pulls(commit_hash=commit_hash):
                    print(f'Ignoring `{branch_name}` because it has open pull requests')
                    continue

                # Move on if branch is base for a pull request
                if self.is_pull_request_base(branch=branch_name):
                    print(f'Ignoring `{branch_name}` because it is the base for a pull request of another branch')
                    continue

                # Move on if last commit is newer than last_commit_age_days
                if self.is_commit_older_than(commit_url=commit_url, older_than_days=last_commit_age_days) is False:
                    print(f'Ignoring `{branch_name}` because last commit is newer than {last_commit_age_days} days')
                    continue

                print(f'Branch `{branch_name}` meets the criteria for deletion')
                deletable_branches.append(branch_name)

                # Exit early if we have reached our branch limit
                if len(deletable_branches) == branch_limit:
                    return deletable_branches

            # Re-request next page
            current_page += 1

            response = requests.get(url=self.get_paginated_branches_url(page=current_page), headers=headers)
            if response.status_code != 200:
                raise RuntimeError(f'Failed to make request to {url}. {response} {response.json()}')

            branches: list = response.json()

        return deletable_branches

    def get_deletable_branches_from_closed_pull_requests(
            self,
            last_commit_age_days: int,
            ignore_branches: list[str],
            allowed_prefixes: list[str],
            branch_limit: int,
    ) -> list[str]:
        if branch_limit < 1:
            return []

        # Default branch might not be protected
        default_branch = self.get_default_branch()

        response = self.fetch_pull_requests()
        if response[1] is None:
            raise RuntimeError("Could not get any pull request info from GraphQL.")
        closed_pull_requests = response[0]
        after_cursor = response[1]
        has_next_page = response[2]

        deletable_branches = []
        branch: dict

        while len(closed_pull_requests) > 0:
            for pull_request in closed_pull_requests:
                html_url = pull_request.get('url')
                updated_at = pull_request.get('updatedAt')
                branch_name = pull_request.get('headRefName')
                head_branch = pull_request.get('headRef')

                print(f'Analyzing pull request {html_url}')
                
                if head_branch is None:
                    print(f'Ignoring {html_url} because head branch is already deleted')
                    continue

                # Immediately discard default branch
                if branch_name == default_branch:
                    print(f'Ignoring `{branch_name}` because it is the default branch')
                    continue
                
                found_ignored_prefix = False
                for prefix in ignore_branches:
                    if branch_name.startswith(prefix):
                        found_ignored_prefix = True
                        break
                if found_ignored_prefix is True:
                    print(f'Ignoring `{branch_name}` because it is on the list of ignored branch prefixes')
                    continue

                # If allowed_prefixes are provided, only consider branches that match one of the prefixes
                if len(allowed_prefixes) > 0:
                    found_prefix = False
                    for prefix in allowed_prefixes:
                        if branch_name.startswith(prefix):
                            found_prefix = True
                    if found_prefix is False:
                        print(f'Ignoring `{branch_name}` because it does not match any provided allowed_prefixes')
                        continue
                
                # Move on if last updated at is newer than last_commit_age_days
                if self.is_updated_at_older_than(updated_at=updated_at, older_than_days=last_commit_age_days) is False:
                    print(f'Ignoring {html_url} because last updated time is newer than {last_commit_age_days} days')
                    continue

                branch = self.get_branch_info(branch=branch_name)

                # Don't delete protected branches
                if branch.get('protected') is True:
                    print(f'Ignoring `{branch_name}` because it is protected')
                    continue

                commit_hash = branch.get('commit', {}).get('sha')
                commit_url = branch.get('commit', {}).get('url')

                # Move on if commit is in an open pull request
                if self.has_open_pulls(commit_hash=commit_hash):
                    print(f'Ignoring `{branch_name}` because it has open pull requests')
                    continue

                # Move on if branch is base for a pull request
                if self.is_pull_request_base(branch=branch_name):
                    print(f'Ignoring `{branch_name}` because it is the base for a pull request of another branch')
                    continue

                # Move on if last commit is newer than last_commit_age_days
                if self.is_commit_older_than(commit_url=commit_url, older_than_days=last_commit_age_days) is False:
                    print(f'Ignoring `{branch_name}` because last commit is newer than {last_commit_age_days} days')
                    continue

                print(f'Branch `{branch_name}` meets the criteria for deletion')
                deletable_branches.append(branch_name)

                # Exit early if we have reached our branch limit
                if len(deletable_branches) == branch_limit:
                    return deletable_branches

            if has_next_page is True:
                response = self.fetch_pull_requests(after_cursor=after_cursor)
                if response[1] is None:
                    # If we can't get any more pull requests, return whatever we have
                    if len(deletable_branches) > 0:
                        print(f'Could not get any more pull requests. Returning {len(deletable_branches)} branches')
                        return deletable_branches
                    else:
                        raise RuntimeError("Could not get any pull request info from GraphQL.")
                closed_pull_requests = response[0]
                after_cursor = response[1]
                has_next_page = response[2]
            else: 
                break

        return deletable_branches

    def delete_branches(self, branches: list[str]) -> None:
        for branch in branches:
            print(f'Deleting branch `{branch}`...')
            url = f'{self.base_url}/repos/{self.repo}/git/refs/heads/{branch.replace("#", "%23")}'

            response = requests.request(method='DELETE', url=url, headers=self.make_headers())
            if response.status_code != 204:
                print(f'Failed to delete branch `{branch}`')
                # Warn if deleting a single branch failed, but continue the rest of the action
                print(f'Failed to make DELETE request to {url}. {response} {response.json()}')

            print(f'Branch `{branch}` DELETED!')

    def get_default_branch(self) -> str:
        url = f'{self.base_url}/repos/{self.repo}'
        headers = self.make_headers()

        response = requests.get(url=url, headers=headers)

        if response.status_code != 200:
            raise RuntimeError('Error: could not determine default branch. This is a big one.')

        return response.json().get('default_branch')
    
    def get_branch_info(self, branch: str):
        url = f'{self.base_url}/repos/{self.repo}/branches/{branch}'
        headers = self.make_headers()

        response = requests.get(url=url, headers=headers)

        if response.status_code == 404:
            return None

        if response.status_code != 200:
            raise RuntimeError(f'Failed to make request to {url}. {response} {response.json()}')

        return response.json()

    def has_open_pulls(self, commit_hash: str) -> bool:
        """
        Returns true if commit is part of an open pull request or the branch is the base for a pull request
        """
        url = f'{self.base_url}/repos/{self.repo}/commits/{commit_hash}/pulls'
        headers = self.make_headers()
        headers['accept'] = 'application/vnd.github.groot-preview+json'

        response = requests.get(url=url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f'Failed to make request to {url}. {response} {response.json()}')

        pull_request: dict
        for pull_request in response.json():
            if pull_request.get('state') == 'open':
                return True

        return False

    def is_pull_request_base(self, branch: str) -> bool:
        """
        Returns true if the given branch is base for another pull request.
        """
        url = f'{self.base_url}/repos/{self.repo}/pulls?base={branch}'
        headers = self.make_headers()
        headers['accept'] = 'application/vnd.github.groot-preview+json'

        response = requests.get(url=url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f'Failed to make request to {url}. {response} {response.json()}')

        return len(response.json()) > 0

    def is_commit_older_than(self, commit_url: str, older_than_days: int):
        response = requests.get(url=commit_url, headers=self.make_headers())
        if response.status_code != 200:
            raise RuntimeError(f'Failed to make request to {commit_url}. {response} {response.json()}')

        commit: dict = response.json().get('commit', {})
        committer: dict = commit.get('committer', {})
        author: dict = commit.get('author', {})

        # Get date of the committer (instead of the author) as the last commit could be old but just applied
        # for instance coming from a merge where the committer is bringing in commits from other authors
        # Fall back to author's commit date if none found for whatever bizarre reason
        commit_date_raw = committer.get('date', author.get('date'))
        if commit_date_raw is None:
            print(f"Warning: could not determine commit date for {commit_url}. Assuming it's not old enough to delete")
            return False

        # Dates are formatted like so: '2021-02-04T10:52:40Z'
        commit_date = datetime.strptime(commit_date_raw, "%Y-%m-%dT%H:%M:%SZ")

        delta = datetime.now() - commit_date
        print(f'Last commit was on {commit_date_raw} ({delta.days} days ago)')

        return delta.days >= older_than_days

    def is_updated_at_older_than(self, updated_at: str, older_than_days: int):
        # Dates are formatted like so: '2021-02-04T10:52:40Z'
        updated_date = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")

        delta = datetime.now() - updated_date
        print(f'PR was last updated on {updated_at} ({delta.days} days ago)')

        return delta.days >= older_than_days

    def make_pull_request_query(self, count: int, after_cursor: str = None):
        query = """
                query {
                    repository(owner: OWNER, name: REPO) {
                        pullRequests(
                            states: CLOSED,
                            first: COUNT,
                            after: AFTER,
                            orderBy: {
                                direction: DESC,
                                field: UPDATED_AT
                            }
                        ) {
                            totalCount
                            nodes {
                                ... on PullRequest {
                                    title
                                    url
                                    updatedAt
                                    headRef {
                                        name
                                    }
                                    headRefName
                                }
                            }
                            pageInfo {
                                hasNextPage,
                                endCursor
                            }
                        }
                    }
                }
                """
        return query.replace(
                "AFTER", '"{}"'.format(after_cursor) if after_cursor else "null"
            ).replace(
                "OWNER", '"{}"'.format(self.owner)
            ).replace(
                "REPO", '"{}"'.format(self.repo.split('/')[-1])
            ).replace(
                "COUNT", str(count)
            )


    def fetch_pull_requests(self, after_cursor: str = None):
        pull_requests = []
        data = {}
        attempts = 4
        
        while attempts > 0:
            try:
                data = self.client.execute(
                    query=self.make_pull_request_query(20, after_cursor),
                    headers={"Authorization": "Bearer {}".format(self.token)},
                )
                if "data" in data:
                    break

            except Exception as e:
                print(f"Error fetching GraphQL result:\n{e}\ndata: {data}")
                attempts -= 1
                if attempts > 0:
                    exponential_backoff_seconds = 3 ** (4 - attempts)
                    print(f"Retrying in {exponential_backoff_seconds} seconds (attempt {4 - attempts})\n")
                    sleep(exponential_backoff_seconds)

        if "data" not in data:
            return ([], None, False)

        for pull_request in data["data"]["repository"]["pullRequests"]["nodes"]:
            pull_requests.append(pull_request)

        after_cursor = data["data"]["repository"]["pullRequests"]["pageInfo"]["endCursor"]
        print(f"after_cursor: {after_cursor}")
        has_next_page = data["data"]["repository"]["pullRequests"]["pageInfo"]["hasNextPage"]

        return (pull_requests, after_cursor, has_next_page)