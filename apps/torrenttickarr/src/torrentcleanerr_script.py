import logging
from datetime import datetime
import qbittorrentapi
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# qBittorrent details
QB_HOST = 'https://qbittorrent.internal.dbcloud.org/'
QB_USERNAME = os.getenv('USERNAME_QB')
QB_PASSWORD = os.getenv('PASSWORD_QB')
DAYS_LIMIT = int(os.getenv('DAYS_LIMIT_QB', '14'))

def main():
    # Instantiate qBittorrent client
    qbt_client = qbittorrentapi.Client(
        host=QB_HOST,
        username=QB_USERNAME,
        password=QB_PASSWORD,
        VERIFY_WEBUI_CERTIFICATE=True
    )

    try:
        qbt_client.auth_log_in()
        logging.info("Successfully logged in to qBittorrent")
        
        # Disable "Trash torrent files" option
        qbt_client.app_set_preferences({"delete_to_trash": False})
        logging.info("Disabled 'Trash torrent files' option")
    except qbittorrentapi.LoginFailed as e:
        logging.error(f"Failed to log in: {e}")
        return

    try:
        # Get all torrents
        torrents = qbt_client.torrents_info()
        
        current_time = datetime.now()

        for torrent in torrents:
            seeding_time_days = torrent.seeding_time / 86400  # Convert seconds to days
            completion = torrent.progress * 100  # Progress as a percentage

            if torrent.progress == 1:  # Check if torrent is completed
                completion_date = datetime.fromtimestamp(torrent.completion_on)
                days_since_completion = (current_time - completion_date).days

                if days_since_completion > DAYS_LIMIT and seeding_time_days > DAYS_LIMIT:
                    logging.info(f"Deleting | Completion: {completion:.2f}% | Seeding: {seeding_time_days:.2f}d  | Completed: {days_since_completion}d | {torrent.name} ")
                    qbt_client.torrents_delete(delete_files=True, torrent_hashes=torrent.hash)
                else:
                    logging.info(f"Keeping | Completion: {completion:.2f}% | Seeding: {seeding_time_days:.2f}d | Completed: {days_since_completion}d | {torrent.name} ")
            else:
                logging.info(f"Keeping | Completion: {completion:.2f}% | Seeding: {seeding_time_days:.2f}d | Completed: No | {torrent.name} ")

    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        qbt_client.auth_log_out()
        logging.info("Logged out from qBittorrent")

if __name__ == "__main__":
    main()