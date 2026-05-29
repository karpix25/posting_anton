(function (window) {
  function createReadinessTracker(definitions, onStateChange) {
    const states = {};
    const order = definitions.map(function (item) { return item.key; });
    const labels = definitions.reduce(function (acc, item) {
      acc[item.key] = item.label;
      states[item.key] = { status: 'idle', error: '' };
      return acc;
    }, {});

    function snapshot() {
      const busy = order.find(function (key) {
        return states[key].status === 'loading' || states[key].status === 'idle';
      });
      const failed = order.find(function (key) { return states[key].status === 'error'; });
      const ready = !busy && !failed;
      return {
        ready: ready,
        status: failed ? 'error' : (busy ? 'loading' : 'ready'),
        currentKey: failed || busy || '',
        currentLabel: labels[failed || busy] || '',
        states: Object.assign({}, states),
      };
    }

    function emit() {
      if (onStateChange) onStateChange(snapshot());
    }

    function set(key, status, error) {
      if (!states[key]) return;
      states[key] = { status: status, error: error || '' };
      emit();
    }

    emit();

    return {
      set: set,
      snapshot: snapshot,
    };
  }

  window.AppReadiness = {
    createReadinessTracker: createReadinessTracker,
  };
})(window);
