# run_notify.py
import sys, logging, os
# make sure project root is on path
sys.path.insert(0, os.path.abspath('.'))

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger()

try:
    from notifications import notify_on_ticket_created
except Exception as e:
    logger.error("Failed to import notifications module: %s", e)
    raise

def main():
    if len(sys.argv) < 2:
        print("Usage: python run_notify.py <ticket_id> [actor_user_id]")
        sys.exit(1)
    ticket_id = int(sys.argv[1])
    actor = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print(f"Calling notify_on_ticket_created(ticket_id={ticket_id}, actor_user_id={actor})")
    try:
        notify_on_ticket_created(ticket_id, actor_user_id=actor)
        print("Done calling notify (check logs above).")
    except Exception as e:
        logger.exception("Error while running notify_on_ticket_created: %s", e)

if __name__ == "__main__":
    main()