from app.queues.redis_client import push_batch_for_delivery, pop_batch_for_delivery

push_batch = push_batch_for_delivery
pop_batch = pop_batch_for_delivery
