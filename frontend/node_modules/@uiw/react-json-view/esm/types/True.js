import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var True = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$True = _useTypesStore.True,
    Comp = _useTypesStore$True === void 0 ? {} : _useTypesStore$True;
  useTypesRender(Comp, props, 'True');
  return null;
};
True.displayName = 'JVR.True';