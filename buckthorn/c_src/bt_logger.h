/* buckthorn/c_src/bt_logger.h */
#ifndef BT_LOGGER_H
#define BT_LOGGER_H

#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
#include <sys/types.h>

#define BT_LOG_LEVEL_DEBUG 1
#define BT_LOG_LEVEL_INFO  2
#define BT_LOG_LEVEL_WARN  3
#define BT_LOG_LEVEL_ERROR 4

#define BT_LOG_VAL_INT   1
#define BT_LOG_VAL_FLOAT 2
#define BT_LOG_VAL_BOOL  3
#define BT_LOG_VAL_STR   4

#define BT_LOG_MAX_KVS   4

typedef struct {
    char key[24];
    uint8_t key_len;
    uint8_t val_type;
    uint8_t str_len;
    union {
        int64_t i_val;
        double f_val;
        int b_val;
        char s_val[48];
    } v;
} bt_log_kv_t;

typedef struct {
    uint64_t ts_ns;
    uint8_t level;
    uint8_t msg_len;
    uint8_t num_kvs;
    char msg[96];
    bt_log_kv_t kvs[BT_LOG_MAX_KVS];
} bt_log_entry_t;

typedef struct {
    /* Cache Line 0: Read-mostly configuration state */
    bt_log_entry_t *slots;
    size_t capacity;
    size_t mask;
    _Atomic int running;
    int fd;
    int owns_fd;

    /* Cache Line 1: Producer-owned state (HTTP worker thread) */
    _Alignas(64) _Atomic size_t write_idx;
    size_t cached_read_idx;
    uint64_t dropped_count;
    pid_t owner_pid;

    /* Cache Line 2: Consumer-owned state (Background flusher pthread) */
    _Alignas(64) _Atomic size_t read_idx;
    pthread_t thread;
} bt_logger_t;

bt_logger_t *bt_logger_create(const char *filepath, size_t capacity);
void bt_logger_flush_and_stop(bt_logger_t *logger);
void bt_logger_destroy(bt_logger_t *logger);

/* Lock-free O(1) slot acquisition and publication for Cython/C producers */
bt_log_entry_t *bt_logger_acquire_slot(bt_logger_t *logger, uint8_t level,
                                       const char *msg, size_t msg_len);
void bt_logger_publish_slot(bt_logger_t *logger);

/* Pure C helper to record HTTP access logs directly from bt_handle_http_bytes */
void bt_logger_log_http_access(
    bt_logger_t *logger,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    int status_code,
    int64_t latency_ns
);

#endif /* BT_LOGGER_H */
