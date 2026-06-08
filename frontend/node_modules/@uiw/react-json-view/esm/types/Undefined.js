import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Undefined = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Undefi = _useTypesStore.Undefined,
    Comp = _useTypesStore$Undefi === void 0 ? {} : _useTypesStore$Undefi;
  useTypesRender(Comp, props, 'Undefined');
  return null;
};
Undefined.displayName = 'JVR.Undefined';