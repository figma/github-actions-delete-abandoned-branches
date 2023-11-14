from src.github import Github
from src.io import Options


def run_action(options: Options) -> list:
    print(f"Starting github action to cleanup old branches. Input: {options}")

    github = Github(repo=options.github_repo, token=options.github_token, base_url=options.github_base_url)

    if options.only_closed_prs is True:
        branches = github.get_deletable_branches_from_closed_pull_requests(
            last_commit_age_days=options.last_commit_age_days,
            ignore_branches=options.ignore_branches,
            allowed_prefixes=options.allowed_prefixes,
            branch_limit=options.branch_limit,
        )
    else:
        branches = github.get_deletable_branches(
            last_commit_age_days=options.last_commit_age_days,
            ignore_branches=options.ignore_branches,
            allowed_prefixes=options.allowed_prefixes,
            branch_limit=options.branch_limit,
        )

    print(f"Branches queued for deletion: {branches}")
    if options.dry_run is False:
        print('This is NOT a dry run, deleting branches')
        github.delete_branches(branches=branches)
    else:
        print('This is a dry run, skipping deletion of branches')

    return branches
