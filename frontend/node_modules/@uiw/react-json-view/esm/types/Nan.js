import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Nan = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Nan = _useTypesStore.Nan,
    Comp = _useTypesStore$Nan === void 0 ? {} : _useTypesStore$Nan;
  useTypesRender(Comp, props, 'Nan');
  return null;
};
Nan.displayName = 'JVR.Nan';