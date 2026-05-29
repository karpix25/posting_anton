(function (window) {
  function createSnapshot(value) {
    return JSON.stringify(value == null ? null : value);
  }

  function createAutosaveManager(options) {
    const save = options.save;
    const debounceMs = options.debounceMs || 900;
    const onStateChange = options.onStateChange || function () {};
    let timer = null;
    let requestId = 0;
    let changeId = 0;
    let lastSavedSnapshot = null;
    let pendingValue = null;
    let pendingSnapshot = null;
    let state = {
      status: 'idle',
      dirty: false,
      saving: false,
      error: '',
      lastSavedAt: null,
    };

    function emit(patch) {
      state = Object.assign({}, state, patch);
      onStateChange(Object.assign({}, state));
    }

    function markSynced(value) {
      const snapshot = createSnapshot(value);
      lastSavedSnapshot = snapshot;
      pendingSnapshot = snapshot;
      pendingValue = JSON.parse(snapshot);
      if (timer) clearTimeout(timer);
      timer = null;
      emit({ status: 'saved', dirty: false, saving: false, error: '', lastSavedAt: Date.now() });
    }

    function schedule(value, meta) {
      const nextSnapshot = createSnapshot(value);
      pendingSnapshot = nextSnapshot;
      pendingValue = JSON.parse(nextSnapshot);
      changeId += 1;
      const scheduledChangeId = changeId;
      if (nextSnapshot === lastSavedSnapshot) {
        emit({ status: 'saved', dirty: false, error: '' });
        return Promise.resolve(Object.assign({}, state));
      }
      if (timer) clearTimeout(timer);
      emit({ status: 'dirty', dirty: true, error: '' });
      return new Promise((resolve) => {
        timer = setTimeout(async function () {
          const currentRequestId = ++requestId;
          const savedChangeId = scheduledChangeId;
          const valueToSave = pendingValue;
          const snapshotToSave = pendingSnapshot;
          timer = null;
          emit({ status: 'saving', saving: true, dirty: true, error: '' });
          try {
            const result = await save(valueToSave, meta || {});
            if (currentRequestId !== requestId || savedChangeId !== changeId) {
              resolve(Object.assign({}, state));
              return;
            }
            lastSavedSnapshot = snapshotToSave;
            emit({ status: 'saved', dirty: false, saving: false, error: '', lastSavedAt: Date.now() });
            resolve(result);
          } catch (error) {
            if (currentRequestId === requestId) {
              emit({
                status: 'error',
                dirty: true,
                saving: false,
                error: error && error.message ? error.message : String(error),
              });
            }
            resolve(Object.assign({}, state));
          }
        }, debounceMs);
      });
    }

    async function flush(value, meta) {
      if (value !== undefined) {
        pendingSnapshot = createSnapshot(value);
        pendingValue = JSON.parse(pendingSnapshot);
      }
      changeId += 1;
      const savedChangeId = changeId;
      if (timer) clearTimeout(timer);
      timer = null;
      const nextSnapshot = pendingSnapshot;
      if (nextSnapshot === lastSavedSnapshot) {
        emit({ status: 'saved', dirty: false, error: '' });
        return Object.assign({}, state);
      }
      const currentRequestId = ++requestId;
      const valueToSave = pendingValue;
      const snapshotToSave = pendingSnapshot;
      emit({ status: 'saving', saving: true, dirty: true, error: '' });
      try {
        const result = await save(valueToSave, meta || {});
        if (currentRequestId === requestId && savedChangeId === changeId) {
          lastSavedSnapshot = snapshotToSave;
          emit({ status: 'saved', dirty: false, saving: false, error: '', lastSavedAt: Date.now() });
        }
        return result;
      } catch (error) {
        if (currentRequestId === requestId) {
          emit({
            status: 'error',
            dirty: true,
            saving: false,
            error: error && error.message ? error.message : String(error),
          });
        }
        throw error;
      }
    }

    return {
      getState: function () { return Object.assign({}, state); },
      markSynced: markSynced,
      schedule: schedule,
      flush: flush,
    };
  }

  window.AppAutosave = {
    createAutosaveManager: createAutosaveManager,
    createSnapshot: createSnapshot,
  };
})(window);
