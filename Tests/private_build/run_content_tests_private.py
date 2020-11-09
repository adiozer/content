from __future__ import print_function
import sys
import time
import argparse
import traceback
from time import sleep
import datetime
from distutils.version import LooseVersion
from typing import Any

import urllib3
import demisto_client.demisto_api
from demisto_client.demisto_api.rest import ApiException

from Tests.test_integration import Docker, check_integration, disable_all_integrations
from demisto_sdk.commands.common.constants import PB_Status
from demisto_sdk.commands.common.tools import print_color, print_error, print_warning, \
    LOG_COLORS, str2bool

from Tests.test_content import SettingsTester, ParallelPrintsManager, DataKeeperTester, \
    print_test_summary, update_test_msg, turn_off_telemetry, \
    create_result_files, get_all_tests, get_instances_ips_and_names, get_server_numeric_version, \
    initialize_queue_and_executed_tests_set, get_test_records_of_given_test_names, \
    extract_filtered_tests, load_conf_files, set_integration_params, collect_integrations, notify_failed_test,\
    SERVER_URL

# Disable insecure warnings
urllib3.disable_warnings()


def options_handler():
    parser = argparse.ArgumentParser(description='Utility for batch action on incidents')
    parser.add_argument('-k', '--apiKey', help='The Demisto API key for the server', required=True)
    parser.add_argument('-s', '--server', help='The server URL to connect to')
    parser.add_argument('-c', '--conf', help='Path to conf file', required=True)
    parser.add_argument('-e', '--secret', help='Path to secret conf file')
    parser.add_argument('-n', '--nightly', type=str2bool, help='Run nightly tests')
    parser.add_argument('-t', '--slack', help='The token for slack', required=True)
    parser.add_argument('-a', '--circleci', help='The token for circleci', required=True)
    parser.add_argument('-b', '--buildNumber', help='The build number', required=True)
    parser.add_argument('-g', '--buildName', help='The build name', required=True)
    parser.add_argument('-sa', '--service_account', help="Path to GCS service account.", required=False)
    parser.add_argument('-i', '--isAMI', type=str2bool, help='is AMI build or not', default=False)
    parser.add_argument('-m', '--memCheck', type=str2bool,
                        help='Should trigger memory checks or not. The slack channel to check the data is: '
                             'dmst_content_nightly_memory_data', default=False)
    parser.add_argument('-d', '--serverVersion', help='Which server version to run the '
                                                      'tests on(Valid only when using AMI)', default="NonAMI")
    parser.add_argument('-l', '--testsList', help='List of specific, comma separated'
                                                  'tests to run')

    options = parser.parse_args()
    tests_settings = SettingsTester(options)
    return tests_settings


def run_test_logic(tests_settings: Any, c: Any, failed_playbooks: list,
                   integrations: list, playbook_id: str, succeed_playbooks: list, test_message: str,
                   test_options: dict, slack: Any, circle_ci: str, build_number: str, server_url: str,
                   demisto_user: str, demisto_pass: str, build_name: str,
                   prints_manager: Any, thread_index: int = 0) -> bool:
    """
    run_test_logic handles the testing of the integration by triggering check_integration. afterwards
    it will check the status of the test and report success or add the failed test to the list of
    failed integrations.

    :param tests_settings: SettingsTester object which contains the test variables
    :param c: Client for connecting to XSOAR via demisto-py
    :param failed_playbooks: List of failed playbooks, additional failed playbooks will be added if
                             they failed.
    :param integrations: List of integrations being tested.
    :param playbook_id: ID of the test playbook being tested.
    :param succeed_playbooks: List of playbooks which have passed tests.
    :param test_message: Name of the playbook/integration being tested. This is reported back in the
                         build and used to print in the console the test being ran.
    :param test_options: Options being passed to the test. PID, Docker Threshold, Timeout, etc.
    :param slack: Slack client used for notifications.
    :param circle_ci: CircleCI token. Used to get name of dev who triggered the build.
    :param build_number: The build number of the CI run. Used in slack message.
    :param server_url: The FQDN of the server tests are being ran on.
    :param demisto_user: Username of the demisto user running the tests.
    :param demisto_pass: Password of the demisto user running the tests.
    :param build_name: Name of the build. (Nightly, etc.)
    :param prints_manager: PrintsManager object used in reporting. Will be deprecated.
    :param thread_index: Integer indicating what thread the test is running on.
    :return: Boolean indicating if the test was successful.
    """
    status, inc_id = check_integration(c, server_url, demisto_user, demisto_pass, integrations, playbook_id, prints_manager,
                                       test_options, thread_index=thread_index)
    if status == PB_Status.COMPLETED:
        prints_manager.add_print_job('PASS: {} succeed'.format(test_message), print_color,
                                     thread_index,
                                     message_color=LOG_COLORS.GREEN)
        succeed_playbooks.append(playbook_id)

    elif status == PB_Status.NOT_SUPPORTED_VERSION:
        not_supported_version_message = 'PASS: {} skipped - not supported version'.format(
            test_message)
        prints_manager.add_print_job(not_supported_version_message, print, thread_index)
        succeed_playbooks.append(playbook_id)

    else:
        error_message = 'Failed: {} failed'.format(test_message)
        prints_manager.add_print_job(error_message, print_error, thread_index)
        playbook_id_with_mock = playbook_id
        playbook_id_with_mock += " (Mock Disabled)"
        failed_playbooks.append(playbook_id_with_mock)
        if not tests_settings.is_local_run:
            notify_failed_test(slack, circle_ci, playbook_id, build_number, inc_id, server_url,
                               build_name)

    succeed = status in (PB_Status.COMPLETED, PB_Status.NOT_SUPPORTED_VERSION)

    return succeed


def run_test(tests_settings: SettingsTester, demisto_user: str, demisto_pass: str,
             failed_playbooks: list, integrations: list, playbook_id: str, succeed_playbooks: list,
             test_message: str, test_options: dict, slack: str, circle_ci: str, build_number: str,
             server_url: str, build_name: str, prints_manager: ParallelPrintsManager,
             thread_index: int = 0) -> None:
    """
    Wrapper for the run_test_logic function. Helps by indicating when the test is starting and ending.

    :param tests_settings: SettingsTester object which contains the test variables
    :param demisto_user: Username of the demisto user running the tests.
    :param demisto_pass: Password of the demisto user running the tests.
    :param failed_playbooks: List of failed playbooks, additional failed playbooks will be added if
                             they failed.
    :param integrations: List of integrations being tested.
    :param playbook_id: ID of the test playbook being tested.
    :param succeed_playbooks: List of playbooks which have passed tests.
    :param test_message: Name of the playbook/integration being tested. This is reported back in the
                         build and used to print in the console the test being ran.
    :param test_options: Options being passed to the test. PID, Docker Threshold, Timeout, etc.
    :param slack: Slack client used for notifications.
    :param circle_ci: CircleCI token. Used to get name of dev who triggered the build.
    :param build_number: The build number of the CI run. Used in slack message.
    :param server_url: The FQDN of the server tests are being ran on.
    :param build_name: Name of the build. (Nightly, etc.)
    :param prints_manager: PrintsManager object used in reporting. Will be deprecated.
    :param thread_index: Integer indicating what thread the test is running on.
    :return: No object is returned.
    """
    start_message = f'------ Test {test_message} start ------'
    client = demisto_client.configure(base_url=server_url, username=demisto_user, password=demisto_pass, verify_ssl=False)
    prints_manager.add_print_job(start_message + ' (Private Build Test)', print, thread_index,
                                 include_timestamp=True)
    run_test_logic(tests_settings, client, failed_playbooks, integrations, playbook_id,
                   succeed_playbooks, test_message, test_options, slack, circle_ci, build_number,
                   server_url, demisto_user, demisto_pass, build_name, prints_manager,
                   thread_index=thread_index)
    prints_manager.add_print_job('------ Test %s end ------\n' % (test_message,), print,
                                 thread_index,
                                 include_timestamp=True)

    return


def run_private_test_scenario(tests_settings: SettingsTester, t: dict, default_test_timeout: int,
                              skipped_tests_conf: set,
                              nightly_integrations: list, skipped_integrations_conf: set,
                              skipped_integration: set, run_all_tests: bool, is_filter_configured: bool,
                              filtered_tests: list, skipped_tests: set, secret_params: dict,
                              failed_playbooks: list, playbook_skipped_integration: set,
                              succeed_playbooks: list, slack: str, circle_ci: str, build_number: str,
                              server: str, build_name: str, server_numeric_version: str, demisto_user: str,
                              demisto_pass: str, demisto_api_key: str, prints_manager: ParallelPrintsManager,
                              thread_index: int = 0):
    """
    Checks to see if test should run given the scenario. If the test should run, it will collect the
    integrations which are required to run the test.

    :param tests_settings: SettingsTester object which contains the test variables
    :param t: Options being passed to the test. PID, Docker Threshold, Timeout, etc.
    :param default_test_timeout: Time in seconds indicating when the test should timeout if no
                                 status is reported.
    :param skipped_tests_conf: Collection of the tests which are skipped.
    :param nightly_integrations: List of integrations which should only be tested on a nightly build.
    :param skipped_integrations_conf: Collection of integrations which are skiped.
    :param skipped_integration: Set of skipped integrations. Currently not used in private.
    :param run_all_tests: Boolean. True if 'Run all tests' is present in the test filter.
    :param is_filter_configured: Boolean indicating if there are items in the test filter.
    :param filtered_tests: List of tests excluded from testing.
    :param skipped_tests: List of skipped tests.
    :param secret_params: Parameters found in the content-test-conf. Used to configure the instance.
    :param failed_playbooks: List of failed playbooks, additional failed playbooks will be added if
                             they failed.
    :param playbook_skipped_integration: Not used.
    :param succeed_playbooks: List of playbooks which have passed tests.
    :param slack: Slack client used for notifications.
    :param circle_ci: CircleCI token. Used to get name of dev who triggered the build.
    :param build_number: The build number of the CI run. Used in slack message.
    :param server: The FQDN of the server tests are being ran on.
    :param build_name: Name of the build. (Nightly, etc.)
    :param server_numeric_version: Version of XSOAR currently installed on the server.
    :param demisto_user: Username of the demisto user running the tests.
    :param demisto_pass: Password of the demisto user running the tests.
    :param demisto_api_key: API key for the demisto instance.
    :param prints_manager: PrintsManager object used in reporting. Will be deprecated.
    :param thread_index: Integer indicating what thread the test is running on.
    :return:
    """
    playbook_id = t['playbookID']
    integrations_conf = t.get('integrations', [])
    instance_names_conf = t.get('instance_names', [])

    test_message = 'playbook: ' + playbook_id

    test_options = {
        'timeout': t.get('timeout', default_test_timeout),
        'memory_threshold': t.get('memory_threshold', Docker.DEFAULT_CONTAINER_MEMORY_USAGE),
        'pid_threshold': t.get('pid_threshold', Docker.DEFAULT_CONTAINER_PIDS_USAGE)
    }

    if not isinstance(integrations_conf, list):
        integrations_conf = [integrations_conf, ]

    if not isinstance(instance_names_conf, list):
        instance_names_conf = [instance_names_conf, ]

    test_skipped_integration, integrations, is_nightly_integration = collect_integrations(
        integrations_conf, skipped_integration, skipped_integrations_conf, nightly_integrations)

    if playbook_id in filtered_tests:
        playbook_skipped_integration.update(test_skipped_integration)

    if not run_all_tests:
        # Skip filtered test
        if is_filter_configured and playbook_id not in filtered_tests:
            return

    # Skip bad test
    if playbook_id in skipped_tests_conf:
        skipped_tests.add(f'{playbook_id} - reason: {skipped_tests_conf[playbook_id]}')
        return

    # Skip integration
    if test_skipped_integration:
        return

    # Skip version mismatch test
    test_from_version = t.get('fromversion', '0.0.0')
    test_to_version = t.get('toversion', '99.99.99')

    if not (LooseVersion(test_from_version) <= LooseVersion(server_numeric_version) <= LooseVersion(test_to_version)):
        prints_manager.add_print_job(f'\n------ Test {test_message} start ------', print, thread_index,
                                     include_timestamp=True)
        warning_message = 'Test {} ignored due to version mismatch (test versions: {}-{})'.format(test_message,
                                                                                                  test_from_version,
                                                                                                  test_to_version)
        prints_manager.add_print_job(warning_message, print_warning, thread_index)
        prints_manager.add_print_job(f'------ Test {test_message} end ------\n', print, thread_index,
                                     include_timestamp=True)
        return

    placeholders_map = {'%%SERVER_HOST%%': server}
    are_params_set = set_integration_params(demisto_api_key, integrations, secret_params, instance_names_conf,
                                            playbook_id, prints_manager, placeholders_map, thread_index=thread_index)
    if not are_params_set:
        failed_playbooks.append(playbook_id)
        return

    test_message = update_test_msg(integrations, test_message)
    run_test(tests_settings, demisto_user, demisto_pass, failed_playbooks, integrations,
             playbook_id, succeed_playbooks, test_message, test_options, slack, circle_ci,
             build_number, server, build_name, prints_manager, thread_index=thread_index)


def execute_testing(tests_settings: SettingsTester, server_ip: str, all_tests: set,
                    tests_data_keeper: DataKeeperTester, prints_manager: ParallelPrintsManager,
                    thread_index: int = 0):
    """
    Main function used to handle the testing process. Starts by turning off telemetry and disabling
    any left over tests. Afterwards it will create a test queue object which then is used to run the
    specific test scenario.

    :param tests_settings: SettingsTester object which contains the test variables
    :param server_ip: IP address of the server. Will be formatted before use.
    :param all_tests: All tests currently in the test conf.
    :param tests_data_keeper: Object containing all the test results. Used by report tests function.
    :param prints_manager: PrintsManager object used in reporting. Will be deprecated.
    :param thread_index: Integer indicating what thread the test is running on.
    :return: No object is returned, just updates the tests_data_keep object.
    """
    server = SERVER_URL.format(server_ip)
    server_numeric_version = tests_settings.serverNumericVersion
    start_message = "Executing tests with the server {} - and the server ip {}".format(server, server_ip)
    prints_manager.add_print_job(start_message, print, thread_index)
    slack = tests_settings.slack
    circle_ci = tests_settings.circleci
    build_number = tests_settings.buildNumber
    build_name = tests_settings.buildName
    conf, secret_conf = load_conf_files(tests_settings.conf_path, tests_settings.secret_conf_path)
    demisto_api_key = tests_settings.api_key
    demisto_user = secret_conf['username']
    demisto_pass = secret_conf['userPassword']

    default_test_timeout = conf.get('testTimeout', 30)

    tests = conf['tests']
    skipped_tests_conf = conf['skipped_tests']
    nightly_integrations = conf['nightly_integrations']
    skipped_integrations_conf = conf['skipped_integrations']
    unmockable_integrations = conf['unmockable_integrations']

    secret_params = secret_conf['integrations'] if secret_conf else []

    filtered_tests, is_filter_configured = extract_filtered_tests(prints_manager)

    if not tests or len(tests) == 0:
        prints_manager.add_print_job('no integrations are configured for test', print, thread_index)
        prints_manager.execute_thread_prints(thread_index)
        return
    xsoar_client = demisto_client.configure(base_url=server, username=demisto_user,
                                            password=demisto_pass, verify_ssl=False)

    # turn off telemetry
    turn_off_telemetry(xsoar_client)

    failed_playbooks = []
    succeed_playbooks = []
    skipped_tests = set([])
    skipped_integration = set([])
    playbook_skipped_integration = set([])

    disable_all_integrations(xsoar_client, prints_manager, thread_index=thread_index)
    prints_manager.execute_thread_prints(thread_index)
    #  Private builds do not use mocking. Here we copy the mocked test list to the unmockable list.
    private_tests = get_test_records_of_given_test_names(tests_settings, all_tests)
    try:
        # first run the mock tests to avoid mockless side effects in container
        prints_manager.add_print_job("\nRunning private tests", print, thread_index)
        executed_in_current_round, private_tests_queue = initialize_queue_and_executed_tests_set(private_tests)
        while not private_tests_queue.empty():
            t = private_tests_queue.get()
            executed_in_current_round = update_round_set_and_sleep_if_round_completed(
                executed_in_current_round, prints_manager, t, thread_index)
            run_private_test_scenario(tests_settings, t, default_test_timeout, skipped_tests_conf,
                                      nightly_integrations, skipped_integrations_conf,
                                      skipped_integration, run_all_tests, is_filter_configured,
                                      filtered_tests, skipped_tests, secret_params,
                                      failed_playbooks, playbook_skipped_integration,
                                      succeed_playbooks, slack, circle_ci, build_number, server,
                                      build_name, server_numeric_version, demisto_user,
                                      demisto_pass, demisto_api_key, prints_manager,
                                      thread_index=thread_index)
            prints_manager.execute_thread_prints(thread_index)

    except Exception as exc:
        if exc.__class__ == ApiException:
            error_message = exc.body
        else:
            error_message = f'~~ Thread {thread_index + 1} failed ~~\n{str(exc)}\n{traceback.format_exc()}'
        prints_manager.add_print_job(error_message, print_error, thread_index)
        prints_manager.execute_thread_prints(thread_index)
        failed_playbooks.append(f'~~ Thread {thread_index + 1} failed ~~')
        raise

    finally:
        tests_data_keeper.add_tests_data(succeed_playbooks, failed_playbooks, skipped_tests,
                                         skipped_integration, unmockable_integrations)


def update_round_set_and_sleep_if_round_completed(executed_in_current_round: set,
                                                  prints_manager: ParallelPrintsManager,
                                                  t: dict,
                                                  thread_index: int) -> set:
    """
    Checks if the string representation of the current test configuration is already in
    the executed_in_current_round set.
    If it is- it means we have already executed this test and the we have reached a round and
    there are tests that
    were not able to be locked by this execution..
    In that case we want to start a new round monitoring by emptying the
    'executed_in_current_round' set and sleep
    in order to let the tests be unlocked
    Args:
        executed_in_current_round: A set containing the string representation of all tests
        configuration as they appear
        in conf.json file that were already executed in the current round
        prints_manager: ParallelPrintsManager object
        t: test configuration as it appears in conf.json file
        thread_index: Currently executing thread

    Returns:
        A new executed_in_current_round set which contains only the current tests configuration if a
        round was completed else it just adds the new test to the set.
    """
    if str(t) in executed_in_current_round:
        prints_manager.add_print_job(
            'all tests in the queue were executed, sleeping for 30 seconds to let locked tests get unlocked.',
            print,
            thread_index)
        executed_in_current_round = set()
        time.sleep(30)
    executed_in_current_round.add(str(t))
    return executed_in_current_round


def manage_tests(tests_settings: SettingsTester):
    """
    This function manages the execution of Demisto's tests.

    Args:
        tests_settings (SettingsTester): An object containing all the relevant data regarding how the
                                        tests should be ran.

    """
    tests_settings.serverNumericVersion = get_server_numeric_version(tests_settings.serverVersion,
                                                                     tests_settings.is_local_run)
    instances_ips = get_instances_ips_and_names(tests_settings)
    number_of_instances = len(instances_ips)
    prints_manager = ParallelPrintsManager(number_of_instances)
    tests_data_keeper = DataKeeperTester()

    for ami_instance_name, ami_instance_ip in instances_ips:
        if ami_instance_name == tests_settings.serverVersion:
            print_color("Starting private testing for {}".format(ami_instance_name), LOG_COLORS.GREEN)
            print("Starts tests with server url - https://{}".format(ami_instance_ip))
            all_tests = get_all_tests(tests_settings)
            execute_testing(tests_settings, ami_instance_ip, all_tests, tests_data_keeper,
                            prints_manager, thread_index=0)
            sleep(8)

    print_test_summary(tests_data_keeper, tests_settings.isAMI)
    create_result_files(tests_data_keeper)

    if tests_data_keeper.failed_playbooks:
        tests_failed_msg = "Some tests have failed. Not destroying instances."
        print(tests_failed_msg)
        sys.exit(1)


def main():
    print("Time is: {}\n\n\n".format(datetime.datetime.now()))
    tests_settings = options_handler()
    manage_tests(tests_settings)


if __name__ == '__main__':
    main()
