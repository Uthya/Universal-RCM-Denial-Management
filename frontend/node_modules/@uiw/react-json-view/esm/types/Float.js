import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Float = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Float = _useTypesStore.Float,
    Comp = _useTypesStore$Float === void 0 ? {} : _useTypesStore$Float;
  useTypesRender(Comp, props, 'Float');
  return null;
};
Float.displayName = 'JVR.Float';