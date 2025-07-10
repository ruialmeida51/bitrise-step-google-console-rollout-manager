import copy
import sys
import httplib2
from apiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials
from oauth2client.client import AccessTokenRefreshError


def get_edit_id(service, package_name):
    """
    Begins an edit transaction and gets the editId for the specified package name.

    Args:
        service: Authorized Google Play Developer API service instance.
        package_name (str): The application package name.

    Returns:
        str: The edit ID for the current transaction.
    """
    edits = service.edits().insert(packageName=package_name).execute()
    return edits['id']


def get_info_for_track(service, edit_id, package_name, track):
    """
    Retrieves information for a given track: status, rollout percentage, release notes, version codes, etc.

    Args:
        service: Authorized Google Play Developer API service instance.
        edit_id (str): The ID of the current edit session.
        package_name (str): The application package name.
        track (str): The track to query (e.g. 'internal', 'production').

    Returns:
        dict: The track release metadata.
    """
    return service.edits().tracks().get(
        editId=edit_id,
        packageName=package_name,
        track=track
    ).execute()


def parse_rollout_steps(rollout_steps_raw):
    """
    Parses and validates a rollout step string (e.g., '1,20,50,100').

    Validates that:
    - All values are comma-separated integers.
    - Each value is between 0 and 100.
    - A value must always be bigger than the previous one.

    Converts valid percentages into float fractions for the Play Console API (e.g., 20 -> 0.2).

    Args:
        rollout_steps_raw (str): Comma-separated string of rollout steps.

    Returns:
        List[float]: A list of rollout fractions (e.g., [0.01, 0.2, 0.5, 1.0]).

    Raises:
        SystemExit: If the format is invalid or constraints are violated.
    """
    try:
        steps = [int(s.strip()) for s in rollout_steps_raw.split(",")]
    except ValueError:
        raise SystemExit("💥 Rollout steps must be comma-separated numbers only (e.g., 1,20,50,100)")

    if any(step < 0 or step > 100 for step in steps):
        raise SystemExit("💥 All rollout steps must be between 0 and 100.")

    if any(steps[i] >= steps[i + 1] for i in range(len(steps) - 1)):
        raise SystemExit("💥 Each rollout step must be strictly greater than the previous (e.g., 1,20,50,100).")

    return [step / 100.0 for step in steps]


def main():
    track = sys.argv[1]
    rollout_increase_steps = sys.argv[2]
    package_name = sys.argv[3]
    service_credentials_file = sys.argv[4]

    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        service_credentials_file,
        scopes='https://www.googleapis.com/auth/androidpublisher'
    )

    http = httplib2.Http()
    http = credentials.authorize(http)

    service = build('androidpublisher', 'v3', http=http)

    try:
        edit_id = get_edit_id(
            service=service,
            package_name=package_name
        )

        track_info = get_info_for_track(
            service=service,
            edit_id=edit_id,
            package_name=package_name,
            track=track
        )

        # Unedited copy of the track. The current status without the edit.
        old_result = copy.deepcopy(track_info)

        print("🚀 Current release status: ", track_info)

        # Check if track has releases
        if 'releases' not in track_info or not track_info['releases']:
            print("⚠️ Track has no releases. Skipping rollout increase.")
            return

        for release in track_info['releases']:
            # Get the release status
            release_status = release['status']
            print("📝 Status is: " + release_status)

            # If it's completed, nothing to do. Carry on.
            if release_status == "completed":
                print("✅ Release is completed. No action needed.")
                return

            # Once again, if it's halted, nothing to do. Carry on.
            if release_status == "halted":
                print("⚠️ Release was halted. Skipping rollout increase.")
                return

            # If it's in progress, we have things to do!
            if release_status == "inProgress":
                print("🚧 Release is in progress, continuing update.")

                # Parse our rollout steps.
                rollout_steps = parse_rollout_steps(rollout_increase_steps)
                print("🪜 Rollout steps are: ", rollout_steps)

                current_rollout_percentage = release['userFraction']
                new_rollout_percentage = None

                # Update our rollout step to the next increment based on the rollout percentage
                for step in rollout_steps:
                    if step > current_rollout_percentage:
                        new_rollout_percentage = step
                        break

                if new_rollout_percentage is None:
                    raise SystemExit("ℹ️ No higher rollout step found. Already at or above maximum configured value.")

                print(
                    f"📝 Attempting to increase rollout from {current_rollout_percentage} to: {new_rollout_percentage}")

                if new_rollout_percentage < 1:
                    print('📝 Updating rollout to', new_rollout_percentage)
                    release['userFraction'] = new_rollout_percentage
                elif new_rollout_percentage == 1:
                    print('📝 Marking rollout completed.')
                    del release['userFraction']
                    release['status'] = 'completed'

                # If we have changes to be applied,
                if old_result != track_info:
                    # Check if we have more than one release to send
                    completed_releases = list(
                        filter(
                            lambda lambda_release: lambda_release['status'] == "completed", track_info['releases']
                        )
                    )
                    # If we do, we want to only update the most recent one which is usually the one that's being rolled out
                    if len(completed_releases) == 2:
                        track_info['releases'].remove(completed_releases[1])

                print("🚀 Updating status to: ", track_info)

                # Update the track with all the updates we did so far.
                service.edits().tracks().update(
                    editId=edit_id,
                    track=track,
                    packageName=package_name,
                    body=track_info
                ).execute()

                # Commit the update to play store.
                commit_request = service.edits().commit(
                    editId=edit_id,
                    packageName=package_name
                ).execute()

                print('✅ Edit ', commit_request['id'], ' has been committed')
    except AccessTokenRefreshError:
        raise SystemExit(
            '💥 The credentials have been revoked or expired, please re-run the application to re-authorize')


if __name__ == '__main__':
    main()
