import requests
import sys
from Tests.test_utils import print_error, print_warning, print_color, LOG_COLORS


def get_base_branch(pr_num) -> str:
    """
    Fetches the base branch name of PR num {pr_num}
    :param pr_num: The PR number
    :return: The name of the base branch if exists
    """

    # Disable insecure warnings
    requests.packages.urllib3.disable_warnings()  # pylint: disable=no-member

    try:
        url = f'https://api.github.com/repos/demisto/content/pulls/{pr_num}'
        res = requests.get(url, verify=False)
        if res.status_code != 200:
            # If we didn't succeed to fetch the pr maybe it doesn't exist - then we don't want the build to fail
            print_warning(f'Unable to fetch PR num {pr_num}')
            return ''
        response = res.json()
        if response and isinstance(response, dict):
            base = response.get('base', {})
            base_branch = base.get('ref')
            if base_branch:
                return base_branch
        elif response and isinstance(response, list) and len(response) == 1:
            pr = response[0]
            base = pr.get('base', {})
            base_branch = base.get('ref')
            if base_branch:
                return base_branch
    except requests.exceptions.ConnectionError as exc:
        # If we didn't succeed to fetch the pr maybe it doesn't exist - then we don't want the build to fail
        print_warning(f'Unable to fetch PR num {pr_num}')
        return ''

    return ''


def check_base_branch(pr_num):
    print_color(f'Starting to fetch the base branch of PR num {pr_num}', LOG_COLORS.GREEN)
    base_branch = get_base_branch(pr_num)
    print_color(f'Finished to fetch the base branch of PR num {pr_num}', LOG_COLORS.GREEN)
    if base_branch == 'master':
        print_error("You cannot merge into master when creating an external PR.")
        sys.exit(1)
    else:
        print_color(f'Base branch of PR num {pr_num} is not master - Great!', LOG_COLORS.GREEN)


def main():
    circle_branch = sys.argv[1]
    # If we run this script the circle branch of format "pull/[0-9]+"
    pr_num = circle_branch.split('pull/')[1]
    check_base_branch(pr_num)


if __name__ == '__main__':
    main()
