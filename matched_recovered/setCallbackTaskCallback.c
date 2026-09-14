#include "common.h"

typedef struct {
    char unk_00[0x8];
    void *callback;
} CallbackTask;

void setCallbackTaskCallback(CallbackTask *task, void *callback) {
    task->callback = callback;
}
